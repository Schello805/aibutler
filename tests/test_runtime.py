from pathlib import Path
import pytest
from deploy.runtime import prepare_runtime,MAX_AGE


def release_at(path):
    path.mkdir()
    (path/'requirements.txt').write_text('example>=1\n')
    (path/'requirements-speech.txt').write_text('speech>=1\n')
    return path


def builder_with(calls):
    def build(path,release,speech,cache):
        calls.append((path,speech))
        (path/'bin').mkdir();(path/'bin/python').write_text('test executable')
        (path/'bin/python').chmod(0o755)
    return build


def test_releases_reuse_completed_runtime_without_modifying_it(tmp_path):
    calls=[];build=builder_with(calls)
    first=release_at(tmp_path/'first');second=release_at(tmp_path/'second');root=tmp_path/'runtimes'
    old=prepare_runtime(root,first,True,builder=build,now=1000)
    original=(old/'.complete.json').read_bytes()
    new=prepare_runtime(root,second,True,builder=build,now=1001)
    assert old==new and len(calls)==1
    assert (old/'.complete.json').read_bytes()==original
    assert old.stat().st_mode & 0o777==0o755


def test_changed_requirements_and_speech_build_new_independent_runtimes(tmp_path):
    calls=[];build=builder_with(calls);release=release_at(tmp_path/'release');root=tmp_path/'runtimes'
    first=prepare_runtime(root,release,True,builder=build,now=1000)
    (release/'requirements.txt').write_text('example>=2\n')
    second=prepare_runtime(root,release,True,builder=build,now=1001)
    third=prepare_runtime(root,release,False,builder=build,now=1002)
    assert len({first,second,third})==3 and all(path.exists() for path in (first,second,third))
    assert len(calls)==3


def test_weekly_and_explicit_refresh_preserve_old_runtime_for_rollback(tmp_path):
    calls=[];build=builder_with(calls);release=release_at(tmp_path/'release');root=tmp_path/'runtimes'
    first=prepare_runtime(root,release,True,builder=build,now=1000)
    second=prepare_runtime(root,release,True,builder=build,now=1000+MAX_AGE)
    third=prepare_runtime(root,release,True,refresh=True,builder=build,now=1001+MAX_AGE)
    assert len({first,second,third})==3
    assert all((path/'bin/python').exists() for path in (first,second,third))


def test_failed_runtime_is_not_reused_and_successful_one_is_untouched(tmp_path):
    calls=[];release=release_at(tmp_path/'release');root=tmp_path/'runtimes'
    previous=prepare_runtime(root,release,True,builder=builder_with(calls),now=1000)
    def fail(path,*args):
        (path/'partial').touch()
        raise RuntimeError('pip failed')
    with pytest.raises(RuntimeError):prepare_runtime(root,release,True,refresh=True,builder=fail,now=1001)
    assert list(root.iterdir())==[previous]
    assert prepare_runtime(root,release,True,builder=fail,now=1002)==previous


def test_python_version_change_requires_new_environment(tmp_path,monkeypatch):
    import deploy.runtime as module
    release=release_at(tmp_path/'release');root=tmp_path/'runtimes';calls=[];build=builder_with(calls)
    first=prepare_runtime(root,release,True,builder=build,now=1000)
    monkeypatch.setattr(module.sys,'version',module.sys.version+' patched')
    second=prepare_runtime(root,release,True,builder=build,now=1001)
    assert first!=second and len(calls)==2
