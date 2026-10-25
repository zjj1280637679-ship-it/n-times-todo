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
        assert len(list((base/'private/versions').glob('*.json')))==4
        assert len(list((base/'private/versions').glob('*.md')))==4
        print(json.dumps({'ok':True,'versions':4,'isolated_install':True,'source_import':False,
                          'covered':['init','add','complete','stale-conflict','replay','restore-as-new','full-pages','verify']}))

if __name__=='__main__':
    main()
