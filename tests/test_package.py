"""Preservation and fail-closed identity tests for the stdlib PPTX merger."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile
import xml.etree.ElementTree as ET


from figure_rebuild import package
from figure_rebuild.paths import python_environment
P, A, R, REL, CT = package.P, package.A, package.R, package.REL, package.CT
NS = package.NS
SIZE = (12191365, 6858000)


def data(element):
    return ET.tostring(element, encoding="utf-8", xml_declaration=True)


def relationships(items):
    root = ET.Element(f"{{{REL}}}Relationships")
    for attrs in items:
        ET.SubElement(root, f"{{{REL}}}Relationship", **attrs)
    return data(root)


def shape(identity, name, *, text=None):
    result = ET.Element(f"{{{P}}}sp")
    nv = ET.SubElement(result, f"{{{P}}}nvSpPr")
    ET.SubElement(nv, f"{{{P}}}cNvPr", id=str(identity), name=name)
    ET.SubElement(nv, f"{{{P}}}cNvSpPr")
    ET.SubElement(nv, f"{{{P}}}nvPr")
    ET.SubElement(result, f"{{{P}}}spPr")
    if text is not None:
        body = ET.SubElement(result, f"{{{P}}}txBody")
        ET.SubElement(body, f"{{{A}}}bodyPr")
        ET.SubElement(body, f"{{{A}}}lstStyle")
        run = ET.SubElement(ET.SubElement(body, f"{{{A}}}p"), f"{{{A}}}r")
        ET.SubElement(run, f"{{{A}}}t").text = text
    return result


def picture(identity, name, rid):
    result = ET.Element(f"{{{P}}}pic")
    nv = ET.SubElement(result, f"{{{P}}}nvPicPr")
    ET.SubElement(nv, f"{{{P}}}cNvPr", id=str(identity), name=name)
    ET.SubElement(nv, f"{{{P}}}cNvPicPr")
    ET.SubElement(nv, f"{{{P}}}nvPr")
    fill = ET.SubElement(result, f"{{{P}}}blipFill")
    ET.SubElement(fill, f"{{{A}}}blip", {f"{{{R}}}embed": rid})
    ET.SubElement(result, f"{{{P}}}spPr")
    return result


def group(identity, name, children):
    result = ET.Element(f"{{{P}}}grpSp")
    nv = ET.SubElement(result, f"{{{P}}}nvGrpSpPr")
    ET.SubElement(nv, f"{{{P}}}cNvPr", id=str(identity), name=name)
    ET.SubElement(nv, f"{{{P}}}cNvGrpSpPr")
    ET.SubElement(nv, f"{{{P}}}nvPr")
    ET.SubElement(result, f"{{{P}}}grpSpPr")
    result.extend(children)
    return result


def connector(identity, name, start_id, end_id):
    result = ET.Element(f"{{{P}}}cxnSp")
    nv = ET.SubElement(result, f"{{{P}}}nvCxnSpPr")
    ET.SubElement(nv, f"{{{P}}}cNvPr", id=str(identity), name=name)
    props = ET.SubElement(nv, f"{{{P}}}cNvCxnSpPr")
    ET.SubElement(props, f"{{{A}}}stCxn", id=str(start_id), idx="0")
    ET.SubElement(props, f"{{{A}}}endCxn", id=str(end_id), idx="0")
    ET.SubElement(nv, f"{{{P}}}nvPr")
    ET.SubElement(result, f"{{{P}}}spPr")
    return result


def slide(objects):
    root = ET.Element(f"{{{P}}}sld")
    tree = ET.SubElement(ET.SubElement(root, f"{{{P}}}cSld"), f"{{{P}}}spTree")
    nv = ET.SubElement(tree, f"{{{P}}}nvGrpSpPr")
    ET.SubElement(nv, f"{{{P}}}cNvPr", id="1", name="")
    ET.SubElement(nv, f"{{{P}}}cNvGrpSpPr")
    ET.SubElement(nv, f"{{{P}}}nvPr")
    ET.SubElement(tree, f"{{{P}}}grpSpPr")
    tree.extend(objects)
    return root


def fixture(slides, *, size=SIZE, extras=None, slide_rels=None, image_type="image/png"):
    # A stable slide ID maps to an arbitrary filename and relationship, so any
    # accidental slide-number/filename inference is visible in the tests.
    presentation = ET.Element(f"{{{P}}}presentation")
    listing = ET.SubElement(presentation, f"{{{P}}}sldIdLst")
    rels, files = [], {}
    for index, (stable_id, part, root) in enumerate(slides):
        rid = f"rIdCustom{index + 20}"
        ET.SubElement(listing, f"{{{P}}}sldId", {"id": str(stable_id), f"{{{R}}}id": rid})
        rels.append({"Id": rid, "Type": R + "/slide", "Target": part.removeprefix("ppt/")})
        files[part] = data(root)
    ET.SubElement(presentation, f"{{{P}}}sldSz", cx=str(size[0]), cy=str(size[1]))
    files["ppt/presentation.xml"] = data(presentation)
    files["ppt/_rels/presentation.xml.rels"] = relationships(rels)
    types = ET.Element(f"{{{CT}}}Types")
    ET.SubElement(types, f"{{{CT}}}Default", Extension="xml", ContentType="application/xml")
    ET.SubElement(types, f"{{{CT}}}Default", Extension="rels", ContentType="application/vnd.openxmlformats-package.relationships+xml")
    ET.SubElement(types, f"{{{CT}}}Default", Extension="png", ContentType=image_type)
    files["[Content_Types].xml"] = data(types)
    for part, rel in (slide_rels or {}).items():
        files[package.relationship_part(part)] = relationships(rel)
    files.update(extras or {})
    return files


def write_archive(path, files):
    with zipfile.ZipFile(path, "w") as archive:
        archive.comment = b"original user deck comment"
        for index, (name, payload) in enumerate(files.items()):
            info = zipfile.ZipInfo(name, date_time=(2020, 2, 3, 4, 5, 6))
            info.compress_type = zipfile.ZIP_STORED if index % 2 else zipfile.ZIP_DEFLATED
            info.comment = b"preserve this member metadata"
            info.external_attr = 0o600 << 16
            archive.writestr(info, payload)


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.base = self.directory / "base.pptx"
        self.overlay = self.directory / "overlay.pptx"
        self.output = self.directory / "v2.pptx"
        self.target = "ppt/slides/arbitrary-target.xml"
        self.base_objects = [shape(2, "locked-title", text="Keep exactly"),
                             picture(9, "replace-me", "rIdOldImage"),
                             shape(40, "locked-footer", text="original footer")]
        self.base_files = fixture(
            [(257, "ppt/slides/first-unrelated.xml", slide([shape(2, "other-slide")])),
             (900, self.target, slide(self.base_objects))],
            extras={"ppt/media/original.png": b"original image bytes",
                    "ppt/notesSlides/notes42.xml": b"<notes>untouched original source notes</notes>",
                    "ppt/theme/theme1.xml": b"<theme>keep me</theme>",
                    "customXml/item1.xml": b"\x00\xffopaque user asset"},
            slide_rels={self.target: [
                {"Id": "rIdOldImage", "Type": R + "/image", "Target": "../media/original.png"},
                {"Id": "rIdNotes", "Type": R + "/notesSlide", "Target": "../notesSlides/notes42.xml"},
                {"Id": "rIdFigureRebuild1", "Type": R + "/slideLayout", "Target": "../slideLayouts/layout.xml"},
            ]})
        self.overlay_part = "ppt/slides/overlay.xml"
        self.overlay_objects = [
            group(2, "camera-scene", [shape(3, "camera"), shape(4, "trajectory"),
                                      connector(5, "camera-track-connection", 3, 4)]),
            picture(6, "scene-thumbnail", "rIdOverlayImage"),
        ]
        self.overlay_files = fixture(
            [(256, self.overlay_part, slide(self.overlay_objects))],
            extras={"ppt/media/generated.png": b"new image bytes"},
            slide_rels={self.overlay_part: [
                {"Id": "rIdOverlayImage", "Type": R + "/image", "Target": "../media/generated.png"},
                {"Id": "rIdLayout", "Type": R + "/slideLayout", "Target": "../slideLayouts/scaffold.xml"},
                {"Id": "rIdNotes", "Type": R + "/notesSlide", "Target": "../notesSlides/scaffold.xml"},
            ]})
        self.write_inputs()

    def write_inputs(self):
        write_archive(self.base, self.base_files)
        write_archive(self.overlay, self.overlay_files)

    def merge(self, **overrides):
        options = dict(base=self.base, overlay=self.overlay, output=self.output,
                       slide_id=900, base_sha256=hashlib.sha256(self.base.read_bytes()).hexdigest(),
                       replace_ids=["replace-me"])
        options.update(overrides)
        return package.merge_overlay(**options)

    def assert_rejected(self, message, **options):
        with self.assertRaisesRegex(package.PackageError, message):
            self.merge(**options)
        self.assertFalse(self.output.exists())
        self.assertFalse(self.output.with_suffix(".merge-receipt.json").exists())

    def test_native_slide_inspection_and_cli(self):
        result = package.inspect_pptx(self.base)
        self.assertEqual([entry["slide_id"] for entry in result["slides"]], [257, 900])
        target = result["slides"][1]
        self.assertEqual(target["part"], self.target)
        self.assertEqual([item["name"] for item in target["objects"]],
                         ["locked-title", "replace-me", "locked-footer"])
        completed = subprocess.run([sys.executable, "-m", "figure_rebuild.package", "inspect", str(self.base)],
                                   capture_output=True, text=True, check=True, env=python_environment())
        self.assertEqual(json.loads(completed.stdout)["sha256"], result["sha256"])
        overlay = package.inspect_pptx(self.overlay)["slides"][0]["objects"]
        self.assertEqual(overlay[1]["parent_name"], "camera-scene")
        self.assertFalse(overlay[1]["top_level"])

    def test_merge_preserves_other_parts_and_remaps_group_ids_and_media(self):
        original_base = self.base.read_bytes()
        receipt = self.merge()
        self.assertEqual(self.base.read_bytes(), original_base)
        with zipfile.ZipFile(self.base) as before, zipfile.ZipFile(self.output) as after:
            changed = {entry["part"] for entry in receipt["changed_members"]}
            for name in before.namelist():
                if name not in changed:
                    self.assertEqual(before.read(name), after.read(name), name)
                    old, new = before.getinfo(name), after.getinfo(name)
                    self.assertEqual(old.date_time, new.date_time)
                    self.assertEqual(old.comment, new.comment)
                    self.assertEqual(old.compress_type, new.compress_type)
                    self.assertEqual(old.external_attr, new.external_attr)
            self.assertEqual(before.comment, after.comment)
            self.assertEqual(after.read("ppt/media/original.png"), b"original image bytes")
            root = ET.fromstring(after.read(self.target))
            tree = root.find("p:cSld/p:spTree", NS)
            summaries = package.native_inventory(tree)
            self.assertEqual([item["name"] for item in summaries if item["top_level"]],
                             ["locked-title", "locked-footer", "camera-scene", "scene-thumbnail"])
            self.assertEqual(data(tree.find("p:sp", NS)), data(self.base_objects[0]))
            ids = [props.get("id") for props in root.findall(".//p:cNvPr", NS)]
            self.assertEqual(len(ids), len(set(ids)))
            self.assertEqual(root.find(".//a:stCxn", NS).get("id"), receipt["native_id_map"]["3"])
            self.assertEqual(root.find(".//a:endCxn", NS).get("id"), receipt["native_id_map"]["4"])
            embed = root.find(".//a:blip", NS).get(f"{{{R}}}embed")
            self.assertEqual(embed, "rIdFigureRebuild2")
            relroot = ET.fromstring(after.read(package.relationship_part(self.target)))
            imported = next(item for item in relroot if item.get("Id") == embed)
            media_path = package.resolve_target(self.target, imported)
            self.assertEqual(after.read(media_path), b"new image bytes")
            self.assertIn("rIdNotes", {item.get("Id") for item in relroot})
        self.assertEqual(receipt["removed_objects"][0]["name"], "replace-me")
        self.assertEqual(set(receipt["ignored_overlay_relationships"]), {"rIdLayout", "rIdNotes"})
        saved = json.loads(self.output.with_suffix(".merge-receipt.json").read_text())
        self.assertEqual(saved["output_sha256"], hashlib.sha256(self.output.read_bytes()).hexdigest())
        self.assertEqual(saved["output_sha256"], receipt["output_sha256"])

    def test_append_without_replacement_and_prefix(self):
        receipt = self.merge(replace_ids=[], prefix="job-001")
        result = package.inspect_pptx(self.output)["slides"][1]["objects"]
        self.assertIn("replace-me", [item["name"] for item in result])
        self.assertIn("job-001:camera", [item["name"] for item in result])
        self.assertEqual(receipt["removed_objects"], [])

    def test_wrong_hash_slide_size_or_stable_slide_rejected(self):
        self.assert_rejected("SHA256 mismatch", base_sha256="f" * 64)
        self.assert_rejected("native slide ID not found", slide_id=1)
        presentation = ET.fromstring(self.overlay_files["ppt/presentation.xml"])
        presentation.find("p:sldSz", NS).set("cx", "99999")
        self.overlay_files["ppt/presentation.xml"] = data(presentation)
        self.write_inputs()
        self.assert_rejected("dimensions differ")

    def test_output_and_receipt_are_never_overwritten(self):
        self.output.write_bytes(b"preexisting result")
        with self.assertRaisesRegex(package.PackageError, "output exists"):
            self.merge()
        self.assertEqual(self.output.read_bytes(), b"preexisting result")
        self.output.unlink()
        receipt = self.output.with_suffix(".merge-receipt.json")
        receipt.write_text("historical receipt")
        with self.assertRaisesRegex(package.PackageError, "receipt exists"):
            self.merge()
        self.assertFalse(self.output.exists())
        self.assertEqual(receipt.read_text(), "historical receipt")

    def test_missing_duplicate_and_ambiguous_replacement_rejected(self):
        self.assert_rejected("missing or ambiguous", replace_ids=["unknown"])
        self.assert_rejected("duplicate replace-id", replace_ids=["replace-me", "replace-me"])
        root = ET.fromstring(self.base_files[self.target])
        root.find("p:cSld/p:spTree", NS).append(shape(90, "replace-me"))
        self.base_files[self.target] = data(root)
        self.write_inputs()
        self.assert_rejected("missing or ambiguous")

    def test_nested_target_requires_complete_group(self):
        root = ET.fromstring(self.base_files[self.target])
        root.find("p:cSld/p:spTree", NS).append(group(90, "protected-scene", [shape(91, "nested-camera")]))
        self.base_files[self.target] = data(root)
        self.write_inputs()
        self.assert_rejected("nested object", replace_ids=["nested-camera"])

    def test_colliding_names_and_duplicate_overlay_ids_rejected(self):
        root = ET.fromstring(self.overlay_files[self.overlay_part])
        root.find(".//p:cNvPr[@name='camera']", NS).set("name", "locked-title")
        self.overlay_files[self.overlay_part] = data(root)
        self.write_inputs()
        self.assert_rejected("collides")
        self.merge(prefix="job-002")
        self.output.unlink()
        self.output.with_suffix(".merge-receipt.json").unlink()
        root.find(".//p:cNvPr[@name='trajectory']", NS).set("id", "3")
        self.overlay_files[self.overlay_part] = data(root)
        self.write_inputs()
        self.assert_rejected("duplicate native shape ID")

    def test_external_and_nonimage_references_rejected(self):
        relpart = package.relationship_part(self.overlay_part)
        root = ET.fromstring(self.overlay_files[relpart])
        relationship = next(item for item in root if item.get("Id") == "rIdOverlayImage")
        relationship.set("TargetMode", "External")
        relationship.set("Target", "https://example.com/image.png")
        self.overlay_files[relpart] = data(root)
        self.write_inputs()
        self.assert_rejected("external relationship")
        relationship.attrib.pop("TargetMode")
        relationship.set("Target", "../media/generated.png")
        relationship.set("Type", R + "/chart")
        self.overlay_files[relpart] = data(root)
        self.write_inputs()
        self.assert_rejected("non-image overlay relationship")

    def test_dangling_retained_animation_and_connector_rejected(self):
        root = ET.fromstring(self.base_files[self.target])
        timing = ET.SubElement(root, f"{{{P}}}timing")
        ET.SubElement(timing, f"{{{P}}}spTgt", spid="9")
        self.base_files[self.target] = data(root)
        self.write_inputs()
        self.assert_rejected("retained animation")
        root.remove(timing)
        root.find("p:cSld/p:spTree", NS).append(connector(45, "kept-connection", 2, 9))
        self.base_files[self.target] = data(root)
        self.write_inputs()
        self.assert_rejected("retained connector")

    def test_content_type_override_preserves_existing_default(self):
        types = ET.fromstring(self.overlay_files["[Content_Types].xml"])
        ET.SubElement(types, f"{{{CT}}}Override", PartName="/ppt/media/generated.png", ContentType="image/x-new-format")
        self.overlay_files["[Content_Types].xml"] = data(types)
        self.write_inputs()
        receipt = self.merge()
        with zipfile.ZipFile(self.output) as archive:
            root = ET.fromstring(archive.read("[Content_Types].xml"))
            png_default = next(item for item in root if item.get("Extension") == "png")
            self.assertEqual(png_default.get("ContentType"), "image/png")
            override = next(item for item in root if item.get("PartName") == "/" + receipt["imported_media"][0]["output_part"])
            self.assertEqual(override.get("ContentType"), "image/x-new-format")

    def test_xml_compatibility_namespace_retained(self):
        source = self.base_files[self.target].decode()
        position = source.index(">", source.index("?>") + 2)
        source = source[:position] + ' xmlns:compat="urn:compat" xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" mc:Ignorable="compat"' + source[position:]
        self.base_files[self.target] = source.encode()
        self.write_inputs()
        self.merge()
        with zipfile.ZipFile(self.output) as archive:
            result = archive.read(self.target)
            self.assertIn(b'xmlns:compat="urn:compat"', result)
            self.assertIn(b'Ignorable="compat"', result)
            ET.fromstring(result)

    def test_unsafe_zip_members_rejected(self):
        self.overlay_files["../../outside.txt"] = b"bad"
        self.write_inputs()
        self.assert_rejected("unsafe ZIP part name")


if __name__ == "__main__":
    unittest.main()
