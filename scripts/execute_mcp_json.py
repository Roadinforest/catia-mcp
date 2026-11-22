"""Validate or execute one whucad-migration JSON using a real stdio MCP client."""
import argparse,asyncio,hashlib,json,os,sys,traceback,math
from datetime import timedelta
from pathlib import Path
from fastjsonschema import compile as make_validator
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
from catia_mcp.server import create_server
from catia_mcp.connection import _application
import catia_mcp.server as server_module

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'whucad_conversion'))
from stateful import arguments


def save(path,value):
    temp=path.with_suffix(path.suffix+'.tmp')
    temp.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8');temp.replace(path)


async def validators():
    tools=await create_server(protocol='whucad-migration').list_tools()
    return {t.name:make_validator(t.inputSchema,use_default=False) for t in tools if t.name!='model_state'}


def validate(program,schemas):
    if program.get('protocol')!='whucad-migration' or program.get('version')!=3:
        raise ValueError('Expected protocol whucad-migration, version 3; legacy ref/match JSON is not accepted')
    calls=program.get('calls')
    if not isinstance(calls,list) or not calls:raise ValueError('calls must be a nonempty list')
    for index,call in enumerate(calls,1):
        try:
            if call['tool'] not in schemas:raise ValueError('Unknown or disallowed tool: '+str(call['tool']))
            schemas[call['tool']](arguments(call,'validation-model'))
        except Exception as exc:raise ValueError(f'Invalid call {index}: {exc}') from exc
    return len(calls)


async def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('program',type=Path);p.add_argument('--output',type=Path)
    p.add_argument('--validate-only',action='store_true');p.add_argument('--settings-dir',type=Path)
    p.add_argument('--allow-nondefault-settings',action='store_true',help='Explicit diagnostic opt-in; output is not default-environment delivery')
    p.add_argument('--keep-open',action='store_true');p.add_argument('--timeout',type=float,default=90)
    p.add_argument('--export-features',action='store_true',help='Export STEP after each feature for sampled acceptance')
    args=p.parse_args()
    if args.timeout<=0:p.error('--timeout must be positive')
    raw=args.program.read_bytes();program=json.loads(raw);count=validate(program,await validators())
    if args.validate_only:
        print(json.dumps(dict(status='schema_validated',calls=count,sample=program.get('sample'),geometry_verified=False),ensure_ascii=False));return 0
    if args.output is None:p.error('--output is required for execution')
    default_settings=(Path(os.environ['APPDATA'])/'DassaultSystemes/CATSettings').resolve()
    expected=(args.settings_dir or default_settings).resolve()
    if expected!=default_settings and not args.allow_nondefault_settings:
        p.error('Non-default CATIA settings require --allow-nondefault-settings (diagnostics only)')
    # Ignore inherited diagnostic binding; check the actual session before adding a document.
    os.environ.pop('CATIA_MCP_EXPECTED_SETTINGS',None)
    app=_application()
    actual=Path(app.SystemService.Environ('CATUserSettingPath')).resolve()
    if actual!=expected:
        raise RuntimeError(f'CATIA settings mismatch: actual={actual}; expected={expected}. No document created. Open the intended CATIA environment first.')
    os.environ['CATIA_MCP_EXPECTED_SETTINGS']=str(expected)
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    # Schema-check all calls before touching CATIA. Bind only the fresh document.
    previous=app.ActiveDocument if app.Documents.Count else None
    doc=app.Documents.Add('Part');trace=[]
    result=dict(status='running',sample=program.get('sample'),program_sha256=hashlib.sha256(raw).hexdigest(),calls_total=count,calls_completed=0,geometry_verified=False)
    result['environment']=dict(settings_dir=str(actual),default_settings=actual==default_settings,diagnostic=args.allow_nondefault_settings)
    result['runtime_paths']=dict(executor=str(Path(__file__).resolve()),mcp_server=str(Path(server_module.__file__).resolve()),python=sys.executable,working_directory=os.getcwd(),converter=os.environ.get('WHUCAD_CONVERTER'),python_path=os.environ.get('PYTHONPATH'))
    save(out/'program.json',program);save(out/'result.json',result)
    try:
        params=StdioServerParameters(command=sys.executable,args=['-m','catia_mcp','--protocol','whucad-migration'],cwd=str(ROOT),env=dict(os.environ))
        with (out/'server-stderr.log').open('w',encoding='utf-8') as stderr:
            async with stdio_client(params,errlog=stderr) as io:
                async with ClientSession(*io) as client:
                    await client.initialize()
                    async def call(tool,kw):
                        response=await client.call_tool(tool,kw,read_timeout_seconds=timedelta(seconds=args.timeout))
                        trace.append(dict(tool=tool,arguments=kw,response=response.model_dump(mode='json')));save(out/'trace.json',trace)
                        if response.isError:raise RuntimeError(response.content)
                        return response.structuredContent or json.loads(response.content[0].text)
                    mid=(await call('model_state',{}))['model_id'];result['model_id']=mid
                    for item in program['calls']:
                        await call(item['tool'],arguments(item,mid));result['calls_completed']+=1
                        if args.export_features and 'feature_index' in item:
                            doc.ExportData(str(out/f"feature-{int(item['feature_index'])}.stp"),'stp')
                        save(out/'result.json',result)
        doc.Part.Update();doc.ExportData(str(out/'model.stp'),'stp')
        doc.Activate()
        viewer=app.ActiveWindow.ActiveViewer
        viewer.Reframe();viewer.Update()
        result['saved_view']=dict(fit_all_in=True,zoom=float(viewer.Viewpoint3D.Zoom),focus_distance=float(viewer.Viewpoint3D.FocusDistance))
        doc.SaveAs(str(out/'model.CATPart'))
        saved_features=doc.Part.MainBody.Shapes.Count
        doc.Close();doc=None
        doc=app.Documents.Open(str(out/'model.CATPart'))
        reopened_features=doc.Part.MainBody.Shapes.Count
        if reopened_features!=saved_features or reopened_features<1:
            raise RuntimeError('Saved CATPart feature count changed or is empty after reopening')
        result['reopen_check']=dict(passed=True,features=reopened_features,settings_dir=str(actual))
        view=app.ActiveWindow.ActiveViewer.Viewpoint3D
        result['reopen_check']['view']=dict(zoom=float(view.Zoom),focus_distance=float(view.FocusDistance))
        result['reopen_check']['view_preserved']=all(math.isclose(result['saved_view'][k],result['reopen_check']['view'][k],rel_tol=1e-6,abs_tol=1e-9) for k in ('zoom','focus_distance'))
        if not result['reopen_check']['view_preserved']:
            raise RuntimeError('Saved fit-to-window view was not preserved after reopening')
        result.update(status='executed',catpart=str(out/'model.CATPart'),step=str(out/'model.stp'))
    except BaseException:
        result.update(status='execution_failed',error=traceback.format_exc())
        try:doc.SaveAs(str(out/'failure.CATPart'))
        except Exception:result['failure_save_error']=traceback.format_exc()
    finally:
        save(out/'result.json',result)
        if not args.keep_open:
            if doc is not None:doc.Close()
            if previous is not None:previous.Activate()
    print(json.dumps(result,ensure_ascii=False));return 0 if result['status']=='executed' else 1


if __name__=='__main__':raise SystemExit(asyncio.run(main()))
