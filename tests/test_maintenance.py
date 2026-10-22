"""Conservative maintenance must preserve weights and populated metadata."""
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import Mock

from civitmatrix.maintenance import header_metadata, merge_missing, repair_metadata, walk_library


class MaintenanceTests(unittest.TestCase):
    def test_preservation_and_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            weight = root / 'private.safetensors'
            header = json.dumps({'__metadata__': {'modelspec.title': 'Private',
                'modelspec.trigger_phrase': 'trigger', 'ss_base_model_version': 'anima'},
                'tensor': {'dtype': 'U8', 'shape': [4], 'data_offsets': [0, 4]}}).encode()
            original = struct.pack('<Q', len(header)) + header + b'data'
            weight.write_bytes(original)
            sidecar = weight.with_suffix('.cm-info.json')
            old = {'ModelName': 'Keep custom title', 'Nsfw': False, 'custom': {'zero': 0}}
            sidecar.write_text(json.dumps(old))
            events = []
            emit = lambda *a, **k: events.append((a, k))
            metadata = header_metadata(weight)
            repair_metadata(weight, metadata, client=None, apply=False, emit=emit)
            self.assertEqual(json.loads(sidecar.read_text()), old)
            repair_metadata(weight, metadata, client=None, apply=True, emit=emit)
            updated = json.loads(sidecar.read_text())
            self.assertEqual(updated['ModelName'], old['ModelName'])
            self.assertEqual(updated['TrainedWords'], ['trigger'])
            self.assertNotIn('ModelId', updated)
            self.assertNotIn('SHA256', updated.get('Hashes', {}))
            self.assertEqual(weight.read_bytes(), original)
            stamp = sidecar.stat().st_mtime_ns
            repair_metadata(weight, metadata, client=None, apply=True, emit=emit)
            self.assertEqual(sidecar.stat().st_mtime_ns, stamp)
            self.assertEqual(merge_missing({'a': False, 'b': 0}, {'a': True, 'b': 9}), {'a': False, 'b': 0})
            alias = root / 'alias'
            alias.symlink_to(root, target_is_directory=True)
            self.assertEqual(walk_library([root, alias], emit), [weight])
            weight.write_bytes(original[:-1])
            with self.assertRaises(ValueError):
                header_metadata(weight)

    def test_heal_rejects_unrelated_by_hash_version(self):
        from civitmatrix.heal_library import heal_library
        from civitmatrix.hash_blake3 import file_blake3_hex
        from civitmatrix.sm_sidecars import build_cm_info
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            weight = root / 'audio_vae.safetensors'
            weight.write_bytes(b'original VAE bytes')
            client = Mock()
            client.get_version_by_hash.return_value = {
                'id': 123, 'modelId': 456, 'files': [{
                    'name': 'unrelated_checkpoint.safetensors', 'primary': True,
                    'hashes': {'BLAKE3': 'different'},
                }],
            }
            counts = heal_library(client=client, out_dir=root,
                                  build_cm_info=build_cm_info, log=lambda _: None)
            self.assertEqual(counts.get('heal_unresolved'), 1)
            client.download.assert_not_called()
            self.assertEqual(weight.read_bytes(), b'original VAE bytes')
            self.assertFalse(weight.with_suffix('.cm-info.json').exists())
            from civitmatrix.heal_library import _pick_file_for_hash
            exact = {'name': 'audio_vae.safetensors', 'hashes': {'BLAKE3': file_blake3_hex(weight)}}
            version = client.get_version_by_hash.return_value
            version['files'].append(exact)
            self.assertEqual(_pick_file_for_hash(version, file_blake3_hex(weight)), exact)

    def test_unverified_api_result_keeps_local_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            weight = Path(directory) / 'audio_vae.pth'
            weight.write_bytes(b'local model')
            client = Mock()
            client.get_version_by_hash.return_value = {
                'id': 123, 'modelId': 456, 'files': [
                    {'name': 'checkpoint.safetensors', 'hashes': {'BLAKE3': 'wrong'}},
                ],
            }
            repair_metadata(weight, {}, client=client, apply=True, emit=Mock())
            result = json.loads(weight.with_suffix('.cm-info.json').read_text())
            self.assertNotIn('ModelId', result)
            self.assertTrue(result['Hashes']['BLAKE3'])
            client.get_model.assert_not_called()
            client.download.assert_not_called()

    def test_hash_conflict_does_not_replace_anything(self):
        with tempfile.TemporaryDirectory() as directory:
            weight = Path(directory) / 'model.pth'
            weight.write_bytes(b'no unpickling allowed')
            sidecar = weight.with_suffix('.cm-info.json')
            original = '{"Hashes": {"BLAKE3": "wrong"}, "custom": 7}'
            sidecar.write_text(original)
            client = Mock()
            repair_metadata(weight, {}, client=client, apply=True, emit=Mock())
            client.get_version_by_hash.assert_not_called()
            client.download.assert_not_called()
            self.assertEqual(sidecar.read_text(), original)
            self.assertEqual(weight.read_bytes(), b'no unpickling allowed')


if __name__ == '__main__':
    unittest.main()
