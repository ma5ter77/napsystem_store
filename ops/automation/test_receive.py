import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('receiver', Path(__file__).with_name('receive-upload.py'))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

class ReceiverTests(unittest.TestCase):
    def send(self, changes=None, data=b'archive', suffix=b''):
        meta={'sha':'a'*40,'sha256':hashlib.sha256(data).hexdigest(),'size':len(data)}
        meta.update(changes or {})
        with tempfile.TemporaryDirectory() as root:
            result=mod.receive(io.BytesIO(json.dumps(meta).encode()+b'\n'+data+suffix),root)
            self.assertEqual(Path(result['directory']).parent,Path(root))
            self.assertEqual(result['status'],'UPLOADED_NOT_INSTALLED')
            self.assertEqual((Path(result['directory'])/('store-'+'a'*40+'.tar.gz')).read_bytes(),data)
    def test_valid(self): self.send()
    def test_refusals(self):
        for patch in [{'sha':'../escape'},{'sha256':'0'*64},{'size':0},{'size':True},{'size':64*1024*1024+1},{'size':100}]:
            with self.subTest(patch=patch),self.assertRaises(ValueError): self.send(patch)
    def test_trailing(self):
        with self.assertRaises(ValueError): self.send(suffix=b'x')

if __name__=='__main__': unittest.main()
