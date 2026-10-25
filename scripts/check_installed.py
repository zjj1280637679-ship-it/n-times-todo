"""Build-artifact smoke: fresh venv, no source import, synthetic records only."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]

def main():
    wheel=ROOT/'dist/n_times_todo-0.1.0-py3-none-any.whl'
    with tempfile.TemporaryDirectory(prefix='n-times-install-') as tmp:
        base=Path(tmp)
        subprocess.run([sys.executable,'-m','venv',str(base/'venv')],check=True)
        binary=base/'venv'/('Scripts' if os.name=='nt' else 'bin')
        python=binary/('python.exe' if os.name=='nt' else 'python')
        subprocess.run([str(python),'-m','pip','install','--no-index','--no-deps',str(wheel)],check=True,stdout=sys.stderr)
        env={k:v for k,v in os.environ.items() if k!='PYTHONPATH'}
        command=[str(python),'-m','n_times_todo.cli','--store',str(base/'private')]
        def run(*arguments,request=None,expected=0):
            result=subprocess.run(command+list(arguments),input=None if request is None else json.dumps(request),
                                  text=True,capture_output=True,cwd=base,env=env)
            assert result.returncode==expected,(result.returncode,result.stdout,result.stderr)
            return json.loads(result.stdout)
        assert len(run('tools')['tools'])==7
        assert run('init')['snapshot']['version']==0
        request={'tool':'todo.update','arguments':{'expected_version':0,'request_id':'install-add',
                 'changes':[{'op':'add','task':{'id':'synthetic','title':'安装测试'}}]}}
        assert run('call',request=request)['data']['version']==1
        assert run('call',request=request)['data']['replayed'] is True
        changed={'tool':'todo.update','arguments':{'expected_version':1,'request_id':'install-done',
                 'changes':[{'op':'edit','id':'synthetic','set':{'status':'done'}}]}}
        assert run('call',request=changed)['data']['changes']['completed']==['synthetic']
        stale={'tool':'todo.update','arguments':{**changed['arguments'],'request_id':'stale'}}
        assert run('call',request=stale,expected=1)['error']['code']=='version_conflict'
        restored={'tool':'todo.restore','arguments':{'version':1,'expected_version':2,'request_id':'install-restore'}}
        assert run('call',request=restored)['data']['version']==3
        assert run('show','--format','json')['tasks']==run('show','--at','1','--format','json')['tasks']
        assert run('verify')['versions']==4
        # Exercise audit from the installed wheel without any source imports.
        missing=base/'never-created'
        failed=subprocess.run([str(python),'-m','n_times_todo.cli','--store',str(missing),'audit'],
                              input='{}',text=True,capture_output=True,cwd=base,env=env)
        assert failed.returncode==1 and json.loads(failed.stdout)['error']['code']=='store_not_found'
        assert not missing.exists()
        context={'goal':{'source':'synthetic:installation','text':'验证安装后的只读导出'},'evidence':[]}
        original_tasks=run('show','--at','1','--format','json')['tasks']
        before={p.relative_to(base/'private'):(p.stat().st_mode,p.stat().st_mtime_ns,p.read_bytes())
                for p in (base/'private').rglob('*') if p.is_file()}
        audit=run('audit','--since','1','--expected-version','3',request=context)
        assert audit['assessment']['status']=='not_evaluated'
        assert audit['current']['version']==3 and audit['baseline']['version']==1
        assert audit['current']['tasks']==original_tasks
        after={p.relative_to(base/'private'):(p.stat().st_mode,p.stat().st_mtime_ns,p.read_bytes())
               for p in (base/'private').rglob('*') if p.is_file()}
        assert before==after
        imported=subprocess.run([str(python),'-c','from n_times_todo import export_audit; assert callable(export_audit)'],
                                cwd=base,env=env,capture_output=True)
        assert imported.returncode==0,imported.stderr
        assert len(list((base/'private/versions').glob('*.json')))==4
        assert len(list((base/'private/versions').glob('*.md')))==4
        print(json.dumps({'ok':True,'versions':4,'isolated_install':True,'source_import':False,
                          'covered':['init','add','complete','stale-conflict','replay','restore-as-new','full-pages','verify',
                                     'audit-cli','audit-python-import','audit-missing-store','audit-no-writes']}))

if __name__=='__main__':
    main()
