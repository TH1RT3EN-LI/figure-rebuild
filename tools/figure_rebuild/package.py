"""Merge a native one-slide reconstruction without rebuilding the base PPTX.

Slide identity is the native p:sldId/@id, never a display number.  Replacement
identity is the unique native cNvPr/@name of a top-level object or group.
Untouched ZIP member payloads and their ZipInfo metadata are copied unchanged.
Only the target slide, its image relationships, content types (when necessary),
and newly imported image parts may change.  The caller owns version snapshots.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
import posixpath
import re
import sys
import urllib.parse
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

P = "http://schemas.openxmlformats.org/presentationml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"
NS = {"p": P, "a": A, "r": R}
IMAGE_REL = R + "/image"
SLIDE_REL = R + "/slide"
SCAFFOLD_RELS = {R + "/slideLayout", R + "/notesSlide"}
OBJECT_KINDS = {"sp", "pic", "grpSp", "cxnSp", "graphicFrame"}
NON_VISUAL = {
    "sp": "nvSpPr", "pic": "nvPicPr", "grpSp": "nvGrpSpPr",
    "cxnSp": "nvCxnSpPr", "graphicFrame": "nvGraphicFramePr",
}


class PackageError(ValueError):
    """Input failed a preservation or identity check; no output is written."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise PackageError(message)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def checked_part_name(name: str) -> str:
    require(bool(name) and "\\" not in name and "\x00" not in name,
            f"unsafe ZIP part name: {name!r}")
    require(not name.startswith("/") and ":" not in name,
            f"unsafe ZIP part name: {name!r}")
    components = name.rstrip("/").split("/")
    require(all(component not in {"", ".", ".."} for component in components),
            f"unsafe ZIP part name: {name!r}")
    return name


@dataclass
class Package:
    path: Path
    source_hash: str
    payloads: dict[str, bytes]
    infos: dict[str, zipfile.ZipInfo]
    comment: bytes

    @classmethod
    def read(cls, path: str | Path) -> "Package":
        path = Path(path).resolve()
        data = path.read_bytes()
        payloads, infos = {}, {}
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                for info in archive.infolist():
                    checked_part_name(info.filename)
                    require(info.filename not in payloads,
                            f"duplicate ZIP member: {info.filename}")
                    require(not info.flag_bits & 1, "encrypted PPTX is unsupported")
                    payloads[info.filename] = archive.read(info)
                    infos[info.filename] = copy.copy(info)
                comment = archive.comment
        except zipfile.BadZipFile as error:
            raise PackageError(f"invalid PPTX ZIP: {path}") from error
        for name in ("[Content_Types].xml", "ppt/presentation.xml",
                     "ppt/_rels/presentation.xml.rels"):
            require(name in payloads, f"missing package part: {name}")
        return cls(path, sha256(data), payloads, infos, comment)


@dataclass
class XmlDocument:
    root: ET.Element
    namespaces: dict[str, str]

    @classmethod
    def parse(cls, data: bytes, part: str) -> "XmlDocument":
        require(not re.search(br"<!\s*(DOCTYPE|ENTITY)\b", data, re.I),
                f"DTD/entity declarations are unsupported: {part}")
        namespaces = {}
        try:
            for _, (prefix, uri) in ET.iterparse(io.BytesIO(data), events=("start-ns",)):
                require(prefix not in namespaces or namespaces[prefix] == uri,
                        f"rebound XML namespace prefix is unsupported: {part}")
                namespaces[prefix] = uri
            parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True, insert_pis=True))
            root = ET.fromstring(data, parser=parser)
        except ET.ParseError as error:
            raise PackageError(f"invalid XML: {part}: {error}") from error
        return cls(root, namespaces)

    def bytes(self) -> bytes:
        # Keep namespace declarations used only by mc:Ignorable or QName-valued
        # attributes: ElementTree normally drops such declarations on serialization.
        for prefix, uri in self.namespaces.items():
            if not re.fullmatch(r"ns\d+", prefix):
                ET.register_namespace(prefix, uri)
        result = ET.tostring(self.root, encoding="utf-8", xml_declaration=True)
        root_start = result.index(b"?>") + 2
        start_end = result.index(b">", root_start)
        header = result[root_start:start_end]
        additions = []
        for prefix, uri in self.namespaces.items():
            name = "xmlns" + (":" + prefix if prefix else "")
            if not re.search(rb"\s" + re.escape(name.encode()) + rb"\s*=", header):
                escaped = uri.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")
                additions.append(f' {name}="{escaped}"'.encode())
        return result[:start_end] + b"".join(additions) + result[start_end:]


def xml(package: Package, part: str) -> XmlDocument:
    require(part in package.payloads, f"missing package part: {part}")
    return XmlDocument.parse(package.payloads[part], part)


def relationship_part(part: str) -> str:
    parent, name = posixpath.split(part)
    return posixpath.join(parent, "_rels", name + ".rels")


def relationship_map(document: XmlDocument) -> dict[str, ET.Element]:
    require(document.root.tag == f"{{{REL}}}Relationships", "invalid relationships root")
    result = {}
    for item in document.root:
        require(item.tag == f"{{{REL}}}Relationship", "unsupported relationship XML child")
        identity = item.get("Id")
        require(bool(identity) and identity not in result,
                f"missing or duplicate relationship ID: {identity}")
        require(bool(item.get("Target")) and bool(item.get("Type")),
                f"incomplete relationship: {identity}")
        result[identity] = item
    return result


def resolve_target(source: str, relationship: ET.Element) -> str:
    mode = relationship.get("TargetMode", "Internal")
    require(mode == "Internal", f"external relationship is unsupported: {relationship.get('Id')}")
    target = relationship.get("Target", "")
    require(not any(character in target for character in ("\\", "\x00", "?", "#")),
            f"unsafe relationship target: {target!r}")
    decoded = urllib.parse.unquote(target)
    require(":" not in decoded and "\\" not in decoded and "\x00" not in decoded,
            f"unsafe relationship target: {target!r}")
    if decoded.startswith("/"):
        result = posixpath.normpath(decoded[1:])
    else:
        result = posixpath.normpath(posixpath.join(posixpath.dirname(source), decoded))
    checked_part_name(result)
    return result


def slide_catalog(package: Package) -> tuple[dict[str, int], list[dict]]:
    presentation = xml(package, "ppt/presentation.xml").root
    size = presentation.find("p:sldSz", NS)
    require(size is not None, "presentation has no slide size")
    try:
        dimensions = {key: int(size.get(key, "")) for key in ("cx", "cy")}
    except ValueError as error:
        raise PackageError("invalid presentation slide size") from error
    require(all(value > 0 for value in dimensions.values()), "invalid presentation slide size")
    relations = relationship_map(xml(package, "ppt/_rels/presentation.xml.rels"))
    catalog, identities, parts = [], set(), set()
    slide_list = presentation.find("p:sldIdLst", NS)
    require(slide_list is not None, "presentation has no slide list")
    for item in slide_list:
        require(item.tag == f"{{{P}}}sldId", "unsupported slide list child")
        try:
            identity = int(item.get("id", ""))
        except ValueError as error:
            raise PackageError("invalid native slide ID") from error
        require(identity >= 256 and identity not in identities,
                f"invalid or duplicate native slide ID: {identity}")
        rid = item.get(f"{{{R}}}id")
        require(rid in relations and relations[rid].get("Type") == SLIDE_REL,
                f"invalid slide relationship: {rid}")
        part = resolve_target("ppt/presentation.xml", relations[rid])
        require(part in package.payloads and part not in parts,
                f"missing or duplicate slide part: {part}")
        catalog.append({"slide_id": identity, "part": part, "relationship_id": rid})
        identities.add(identity)
        parts.add(part)
    return dimensions, catalog


def shape_tree(root: ET.Element, part: str) -> ET.Element:
    require(root.tag == f"{{{P}}}sld", f"invalid slide XML root: {part}")
    tree = root.find("p:cSld/p:spTree", NS)
    require(tree is not None, f"slide has no shape tree: {part}")
    return tree


def kind(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1] if isinstance(element.tag, str) else "#comment"


def native_properties(element: ET.Element) -> ET.Element | None:
    nv = NON_VISUAL.get(kind(element))
    return element.find(f"p:{nv}/p:cNvPr", NS) if nv else None


def object_summary(element: ET.Element) -> dict:
    props = native_properties(element)
    require(props is not None, f"native object has no cNvPr: {kind(element)}")
    return {"name": props.get("name"), "native_id": props.get("id"), "kind": kind(element)}


def native_inventory(tree: ET.Element) -> list[dict]:
    result = []
    for child in tree:
        if kind(child) not in OBJECT_KINDS:
            continue
        summary = object_summary(child)
        result.append({**summary, "top_level": True, "parent_name": None})
        for descendant in child.iter():
            if descendant is child or kind(descendant) not in OBJECT_KINDS:
                continue
            result.append({**object_summary(descendant), "top_level": False,
                           "parent_name": summary["name"]})
    return result


def inspect_pptx(base: str | Path) -> dict:
    package = Package.read(base)
    dimensions, catalog = slide_catalog(package)
    for slide in catalog:
        tree = shape_tree(xml(package, slide["part"]).root, slide["part"])
        slide["objects"] = native_inventory(tree)
    return {"schema_version": 1, "base": str(package.path),
            "sha256": package.source_hash, "slide_size_emu": dimensions, "slides": catalog}


def content_type(package: Package, part: str) -> str:
    document = xml(package, "[Content_Types].xml")
    require(document.root.tag == f"{{{CT}}}Types", "invalid content types root")
    overrides = [entry.get("ContentType") for entry in document.root
                 if entry.tag == f"{{{CT}}}Override" and entry.get("PartName") == "/" + part]
    require(len(overrides) <= 1, f"ambiguous content type: {part}")
    extension = PurePosixPath(part).suffix[1:].lower()
    defaults = [entry.get("ContentType") for entry in document.root
                if entry.tag == f"{{{CT}}}Default" and entry.get("Extension", "").lower() == extension]
    require(len(defaults) <= 1, f"ambiguous content type extension: {extension}")
    selected = overrides or defaults
    require(bool(selected) and bool(selected[0]), f"missing content type: {part}")
    return selected[0]


def ensure_content_type(document: XmlDocument, part: str, mime: str) -> bool:
    root = document.root
    require(root.tag == f"{{{CT}}}Types", "invalid base content types root")
    overrides = [entry for entry in root if entry.tag == f"{{{CT}}}Override"
                 and entry.get("PartName") == "/" + part]
    require(len(overrides) <= 1, f"ambiguous base content type: {part}")
    if overrides:
        require(overrides[0].get("ContentType") == mime,
                f"base image content type conflict: {part}")
        return False
    extension = PurePosixPath(part).suffix[1:]
    defaults = [entry for entry in root if entry.tag == f"{{{CT}}}Default"
                and entry.get("Extension", "").lower() == extension.lower()]
    require(len(defaults) <= 1, f"ambiguous base content type extension: {extension}")
    if defaults and defaults[0].get("ContentType") == mime:
        return False
    if not defaults:
        ET.SubElement(root, f"{{{CT}}}Default", Extension=extension, ContentType=mime)
    else:
        ET.SubElement(root, f"{{{CT}}}Override", PartName="/" + part, ContentType=mime)
    return True


def referenced_relationships(objects: list[ET.Element]) -> set[str]:
    return {value for item in objects for element in item.iter()
            for key, value in element.attrib.items() if key.startswith("{" + R + "}")}


def validate_native_ids(root: ET.Element, label: str) -> dict[str, ET.Element]:
    result = {}
    for props in root.findall(".//p:cNvPr", NS):
        identity = props.get("id", "")
        require(identity.isdecimal() and int(identity) > 0 and identity not in result,
                f"invalid or duplicate native shape ID in {label}: {identity}")
        result[identity] = props
    return result


def validate_overlay_object(element: ET.Element) -> None:
    object_kind = kind(element)
    require(object_kind in OBJECT_KINDS, f"unsupported overlay object: {object_kind}")
    require(native_properties(element) is not None,
            f"overlay object has no native properties: {object_kind}")
    if object_kind == "graphicFrame":
        graphic_data = element.find("a:graphic/a:graphicData", NS)
        require(graphic_data is not None and graphic_data.get("uri") == A + "/table",
                "only native table graphicFrames are supported")
    require(element.find(".//p:oleObj", NS) is None, "embedded OLE objects are unsupported")
    if object_kind == "grpSp":
        for child in element:
            if kind(child) not in {"nvGrpSpPr", "grpSpPr", "extLst"}:
                validate_overlay_object(child)


def merge_overlay(*, base: str | Path, overlay: str | Path, output: str | Path,
                  slide_id: int, base_sha256: str, replace_ids=(), prefix: str | None = None,
                  receipt_path: str | Path | None = None) -> dict:
    """Validate, append/remap native objects, then write a fresh PPTX and receipt."""
    base_path, overlay_path, output_path = (Path(value).resolve() for value in (base, overlay, output))
    receipt_path = Path(receipt_path).resolve() if receipt_path else output_path.with_suffix(".merge-receipt.json")
    require(output_path not in {base_path, overlay_path}, "output must differ from both inputs")
    require(receipt_path not in {base_path, overlay_path, output_path}, "receipt path conflicts with PPTX input/output")
    require(not output_path.exists(), f"output exists; choose a fresh version: {output_path}")
    require(not receipt_path.exists(), f"receipt exists; choose a fresh version: {receipt_path}")
    require(bool(re.fullmatch(r"[a-fA-F0-9]{64}", base_sha256)), "base-sha256 must be a SHA256 hex digest")
    replacements = list(replace_ids)
    require(all(isinstance(name, str) and bool(name) for name in replacements), "replace-id cannot be empty")
    require(len(replacements) == len(set(replacements)), "duplicate replace-id arguments")
    require(prefix is None or bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", prefix)),
            "prefix must be a short stable ID without whitespace")
    base_package, overlay_package = Package.read(base_path), Package.read(overlay_path)
    require(base_package.source_hash == base_sha256.lower(), "base SHA256 mismatch; re-inspect the current base")
    dimensions, catalog = slide_catalog(base_package)
    overlay_dimensions, overlay_catalog = slide_catalog(overlay_package)
    require(dimensions == overlay_dimensions, "base and overlay slide dimensions differ")
    require(len(overlay_catalog) == 1, "overlay must contain exactly one slide")
    targets = [slide for slide in catalog if slide["slide_id"] == slide_id]
    require(len(targets) == 1, f"native slide ID not found: {slide_id}")
    target_part, overlay_part = targets[0]["part"], overlay_catalog[0]["part"]
    target_document, overlay_document = xml(base_package, target_part), xml(overlay_package, overlay_part)
    tree, overlay_tree = shape_tree(target_document.root, target_part), shape_tree(overlay_document.root, overlay_part)
    original_base_ids = validate_native_ids(target_document.root, "base target slide")
    overlay_ids = validate_native_ids(overlay_document.root, "overlay slide")
    require(overlay_document.root.find("p:timing", NS) is None,
            "overlay animation is unsupported; use a separately reviewed animation workflow")
    objects = []
    for child in overlay_tree:
        if kind(child) in {"nvGrpSpPr", "grpSpPr"}:
            continue
        validate_overlay_object(child)
        objects.append(copy.deepcopy(child))
    require(bool(objects), "overlay contains no native objects")
    removed = []
    for name in replacements:
        matches = [child for child in tree if native_properties(child) is not None
                   and native_properties(child).get("name") == name]
        if not matches and any(props.get("name") == name for props in tree.findall(".//p:cNvPr", NS)):
            raise PackageError(f"replace-id names a nested object; name its complete top-level group: {name}")
        require(len(matches) == 1, f"replace-id is missing or ambiguous: {name}")
        removed.append(matches[0])
    removed_ids = {props.get("id") for item in removed for props in item.findall(".//p:cNvPr", NS)}
    for item in removed:
        tree.remove(item)
    # Retained animations/connectors must not become dangling when a target is removed.
    for element in target_document.root.iter():
        if element.tag == f"{{{P}}}spTgt":
            require(element.get("spid") not in removed_ids,
                    "replacement is targeted by retained animation; explicit animation merge is required")
        if element.tag in {f"{{{A}}}stCxn", f"{{{A}}}endCxn"}:
            require(element.get("id") not in removed_ids,
                    "replacement is targeted by a retained connector; replace its complete group")
    retained_ids = validate_native_ids(target_document.root, "retained base slide")
    retained_names = {props.get("name") for props in retained_ids.values()}
    added_props = [props for item in objects for props in item.findall(".//p:cNvPr", NS)]
    old_names = [props.get("name") for props in added_props]
    require(all(old_names) and len(old_names) == len(set(old_names)),
            "overlay native names must be nonempty and unique, including grouped objects")
    name_map = {name: f"{prefix}:{name}" if prefix else name for name in old_names}
    require(not set(name_map.values()) & retained_names,
            "overlay native name collides with a retained base object; use --prefix")
    # Do not reuse the numeric identities of deleted objects in the same version.
    next_id = max(map(int, original_base_ids), default=0) + 1
    id_map = {props.get("id"): str(next_id + offset) for offset, props in enumerate(added_props)}
    require(set(id_map) <= set(overlay_ids), "overlay object ID inventory mismatch")
    for item in objects:
        for element in item.iter():
            if element.tag == f"{{{P}}}cNvPr":
                element.set("id", id_map[element.get("id")])
                element.set("name", name_map[element.get("name")])
            if element.tag in {f"{{{A}}}stCxn", f"{{{A}}}endCxn"}:
                require(element.get("id") in id_map,
                        "overlay connector refers outside the imported object set")
                element.set("id", id_map[element.get("id")])
    overlay_rel_part = relationship_part(overlay_part)
    if overlay_rel_part in overlay_package.payloads:
        overlay_relations = relationship_map(xml(overlay_package, overlay_rel_part))
    else:
        overlay_relations = {}
    references = referenced_relationships(objects)
    require(references <= set(overlay_relations), "overlay object has a missing relationship")
    # Exporter layout/notes scaffold is deliberately not transplanted into the base.
    ignored_relations = []
    for rid, relationship in overlay_relations.items():
        if rid in references:
            require(relationship.get("Type") == IMAGE_REL,
                    f"referenced non-image overlay relationship is unsupported: {rid}")
            resolve_target(overlay_part, relationship)  # rejects external media
        else:
            require(relationship.get("Type") in SCAFFOLD_RELS | {IMAGE_REL},
                    f"unsupported unused overlay relationship: {rid}")
            ignored_relations.append(rid)
    target_rel_part = relationship_part(target_part)
    if target_rel_part in base_package.payloads:
        target_rel_document = xml(base_package, target_rel_part)
    else:
        target_rel_document = XmlDocument(ET.Element(f"{{{REL}}}Relationships"), {"": REL})
    target_relations = relationship_map(target_rel_document)
    files = dict(base_package.payloads)
    type_document = xml(base_package, "[Content_Types].xml")
    type_changed, relation_map, media, media_mimes = False, {}, [], {}
    for old_rid in sorted(references):
        relationship = overlay_relations[old_rid]
        source_part = resolve_target(overlay_part, relationship)
        require(source_part in overlay_package.payloads, f"missing overlay image part: {source_part}")
        mime = content_type(overlay_package, source_part)
        require(mime.startswith("image/"), f"overlay relationship does not target an image: {source_part}")
        suffix = PurePosixPath(source_part).suffix.lower()
        require(bool(re.fullmatch(r"\.[a-z0-9]+", suffix)), f"unsupported image extension: {source_part}")
        payload = overlay_package.payloads[source_part]
        destination = f"ppt/media/figure-rebuild-{sha256(payload)}{suffix}"
        require(destination not in files or files[destination] == payload,
                f"imported image part collision: {destination}")
        require(destination not in media_mimes or media_mimes[destination] == mime,
                f"inconsistent imported image MIME type: {destination}")
        files[destination] = payload
        media_mimes[destination] = mime
        type_changed = ensure_content_type(type_document, destination, mime) or type_changed
        number = 1
        while f"rIdFigureRebuild{number}" in target_relations:
            number += 1
        new_rid = f"rIdFigureRebuild{number}"
        target = posixpath.relpath(destination, posixpath.dirname(target_part))
        new_relationship = ET.SubElement(target_rel_document.root, f"{{{REL}}}Relationship",
                                         Id=new_rid, Type=IMAGE_REL, Target=target)
        target_relations[new_rid] = new_relationship
        relation_map[old_rid] = new_rid
        media.append({"overlay_part": source_part, "output_part": destination,
                      "sha256": sha256(payload), "content_type": mime,
                      "relationship_id": new_rid})
    for item in objects:
        for element in item.iter():
            for key, value in list(element.attrib.items()):
                if key.startswith("{" + R + "}"):
                    element.set(key, relation_map[value])
        tree.append(item)
    # Imported extension namespaces must survive QName-valued compatibility metadata.
    for prefix_name, uri in overlay_document.namespaces.items():
        if prefix_name not in target_document.namespaces:
            target_document.namespaces[prefix_name] = uri
    files[target_part] = target_document.bytes()
    if references:
        files[target_rel_part] = target_rel_document.bytes()
    if type_changed:
        files["[Content_Types].xml"] = type_document.bytes()
    allowed_changes = {target_part, target_rel_part, "[Content_Types].xml"} | set(media_mimes)
    changed = []
    for name, payload in files.items():
        before = base_package.payloads.get(name)
        if before != payload:
            require(name in allowed_changes, f"unexpected package mutation: {name}")
            changed.append({"part": name, "before_sha256": sha256(before) if before is not None else None,
                            "after_sha256": sha256(payload)})
    require(sha256(base_path.read_bytes()) == base_package.source_hash,
            "base changed during merge; re-inspect and retry")
    require(sha256(overlay_path.read_bytes()) == overlay_package.source_hash,
            "overlay changed during merge; retry")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation is the final guard against concurrent overwrite.
    try:
        with output_path.open("xb") as stream, zipfile.ZipFile(stream, "w") as archive:
            archive.comment = base_package.comment
            for name, payload in files.items():
                info = base_package.infos.get(name)
                if info is None:
                    info = zipfile.ZipInfo(name)
                    info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(copy.copy(info), payload)
    except FileExistsError as error:
        raise PackageError(f"output exists: {output_path}") from error
    receipt = {
        "schema_version": 1, "base": str(base_path), "base_sha256": base_package.source_hash,
        "overlay": str(overlay_path), "overlay_sha256": overlay_package.source_hash,
        "output": str(output_path), "output_sha256": sha256(output_path.read_bytes()),
        "slide_id": slide_id, "slide_part": target_part, "slide_size_emu": dimensions,
        "removed_objects": [{**object_summary(item), "native_ids": [props.get("id")
                             for props in item.findall(".//p:cNvPr", NS)]} for item in removed],
        "added_objects": [{**object_summary(item), "native_ids": [props.get("id")
                           for props in item.findall(".//p:cNvPr", NS)]} for item in objects],
        "native_id_map": id_map, "native_name_map": name_map,
        "relationship_id_map": relation_map, "imported_media": media,
        "ignored_overlay_relationships": ignored_relations,
        "changed_members": changed,
        "preserved_member_count": len(base_package.payloads) - sum(
            change["before_sha256"] is not None for change in changed),
        "preservation": "Untouched ZIP member payloads and ZipInfo metadata retained; base archive not rebuilt semantically.",
    }
    try:
        with receipt_path.open("x", encoding="utf-8") as stream:
            json.dump(receipt, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    except FileExistsError as error:
        raise PackageError(f"PPTX written, but receipt path appeared concurrently: {receipt_path}") from error
    return receipt


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    inspection = subcommands.add_parser("inspect", help="list native slide IDs and object names")
    inspection.add_argument("base", type=Path)
    merging = subcommands.add_parser("merge", help="append a native overlay to one stable slide ID")
    merging.add_argument("--base", type=Path, required=True)
    merging.add_argument("--overlay", type=Path, required=True)
    merging.add_argument("--output", type=Path, required=True)
    merging.add_argument("--slide-id", type=int, required=True)
    merging.add_argument("--base-sha256", required=True)
    merging.add_argument("--replace-id", action="append", default=[])
    merging.add_argument("--prefix")
    merging.add_argument("--receipt", type=Path)
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "inspect":
            result = inspect_pptx(arguments.base)
        else:
            result = merge_overlay(base=arguments.base, overlay=arguments.overlay, output=arguments.output,
                                   slide_id=arguments.slide_id, base_sha256=arguments.base_sha256,
                                   replace_ids=arguments.replace_id, prefix=arguments.prefix,
                                   receipt_path=arguments.receipt)
    except (PackageError, OSError) as error:
        parser.exit(2, f"figure_rebuild package: {error}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
