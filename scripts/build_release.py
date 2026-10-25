"""Offline reproducible wheel + instruction-only plugin ZIP builder."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'dist'

def main():
    OUT.mkdir(exist_ok=True)
    env = {**os.environ, 'SOURCE_DATE_EPOCH':'946684800', 'PIP_NO_INDEX':'1', 'PIP_DISABLE_PIP_VERSION_CHECK':'1'}
    subprocess.run([sys.executable,'-m','pip','wheel','--no-deps','--no-build-isolation',str(ROOT),'-w',str(OUT)],
                   env=env,check=True,stdout=sys.stderr)
    target = OUT/'n-times-todo-plugin-0.1.0.zip'
    with zipfile.ZipFile(target,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        for relative in ('plugin.json','skills/n-times-todo/SKILL.md'):
            info=zipfile.ZipInfo(relative,date_time=(2000,1,1,0,0,0))
            info.compress_type=zipfile.ZIP_DEFLATED
            info.external_attr=0o100644 << 16
            archive.writestr(info,(ROOT/'plugin'/relative).read_bytes())
    artifacts=[]
    for path in (OUT/'n_times_todo-0.1.0-py3-none-any.whl',target):
        data=path.read_bytes()
        artifacts.append({'name':path.name,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()})
    print(json.dumps({'artifacts':artifacts},indent=2))

if __name__=='__main__':
    main()
