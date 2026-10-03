import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from figure_rebuild import publish as pub


class PublicationChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.source = self.root / 'checked.pptx'; self.source.write_bytes(b'complete validated package')
        self.output = self.root / 'exports/final.pptx'; self.receipt = self.root / 'run/delivery.json'
        self.data = {'sha256': hashlib.sha256(self.source.read_bytes()).hexdigest(), 'output': str(self.output.resolve())}

    def tearDown(self): self.tmp.cleanup()

    def publish(self): pub.publish(self.source, self.output, self.receipt, self.data)

    def test_published_file_and_receipt_match_validated_bytes(self):
        self.publish()
        self.assertEqual(self.output.read_bytes(), self.source.read_bytes())
        self.assertEqual(json.loads(self.receipt.read_text()), self.data)
        self.assertFalse(list(self.root.glob('**/.*-*')))

    def test_second_link_failure_leaves_no_partial_delivery(self):
        link = os.link
        def fault(src, dst):
            if Path(dst) == self.output: raise OSError('injected final commit failure')
            link(src, dst)
        with patch.object(pub.os, 'link', side_effect=fault), self.assertRaises(OSError): self.publish()
        self.assertFalse(self.output.exists()); self.assertFalse(self.receipt.exists())
        self.assertFalse(list(self.root.glob('**/.*-*')))

    def test_existing_output_and_receipt_cannot_be_overwritten(self):
        self.publish(); before = self.receipt.read_bytes()
        with self.assertRaises(FileExistsError): self.publish()
        self.assertEqual(self.output.read_bytes(), self.source.read_bytes())
        self.assertEqual(self.receipt.read_bytes(), before)

    def test_hash_mismatch_fails_before_publication(self):
        self.data['sha256'] = '0' * 64
        with self.assertRaises(ValueError): self.publish()
        self.assertFalse(self.output.exists()); self.assertFalse(self.receipt.exists())


if __name__ == '__main__': unittest.main()
