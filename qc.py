"""Quality Control (QC) & Standardization Engine for Web Novel EPUBs.
Standardizes:
1. Synopsis & Metadata: Clean single-page card with title, author, source, and formatted synopsis.
2. Chapter Hierarchy: Standardized chapter numbering, clean volume/arc titles, and subtitle formatting.
3. Blank Space & Spacing QC: Eliminates redundant <p class="blank">, empty paragraphs, and whitespace bloat.
4. Scene Breaks: Replaces messy text breaks (***, ---, ◆◆◆) with elegant divider ornaments.
5. Typography: Minimalist WTR-grade styling (Georgia/serif, 1.65 line-height, justified text).
"""

import os
import sys
import re
import html
import shutil
import zipfile
import pathlib
import tempfile
from typing import Dict, List, Optional, Tuple, Any

# Standard Minimalist WTR Typography CSS
WTR_STANDARD_CSS = """/* WTR Minimalist Standard EPUB Stylesheet */
@namespace epub "http://www.idpf.org/2007/ops";

body {
    font-family: "Georgia", "Times New Roman", "Source Han Serif SC", "Noto Serif CJK JP", serif;
    line-height: 1.68;
    margin: 0 4%;
    padding: 0;
    text-align: justify;
    color: #111111;
    background-color: #fafafa;
}

/* Headings */
h1, h2, h3 {
    font-family: "Georgia", "Times New Roman", serif;
    font-weight: 700;
    color: #1a1a1a;
}

/* Chapter Title & Header */
.chapter-header {
    margin-top: 1.8em;
    margin-bottom: 1.4em;
    text-align: center;
    page-break-before: always;
}

.volume-tag {
    display: block;
    font-size: 0.82em;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: #777777;
    margin-bottom: 0.4em;
    font-weight: 600;
}

.chapter-title {
    font-size: 1.35em;
    line-height: 1.35;
    margin: 0;
    color: #111111;
}

/* Paragraphs & Spacing */
p {
    margin-top: 0;
    margin-bottom: 0.78em;
    text-indent: 0;
    word-break: break-word;
}

/* Scene Breaks */
.scene-divider {
    text-align: center;
    margin: 1.4em 0;
    color: #888888;
    font-size: 0.9em;
    letter-spacing: 0.3em;
}

hr.scene-break {
    border: 0;
    border-top: 1px dashed #cccccc;
    margin: 1.5em auto;
    width: 35%;
}

/* Notes & Sections */
.preface, .afterword {
    font-size: 0.9em;
    color: #444444;
    background-color: #f4f4f4;
    border-left: 3px solid #888888;
    padding: 0.8em 1.2em;
    margin: 1.4em 0;
}

.section-label {
    font-weight: bold;
    font-size: 0.85em;
    color: #666666;
    margin-bottom: 0.4em;
    text-transform: uppercase;
    letter-spacing: 0.05em;
}

/* Single-Page Synopsis Card */
.info-card {
    text-align: center;
    padding: 2.5em 1em 1em 1em;
    page-break-after: always;
}

.novel-title {
    font-size: 1.75em;
    line-height: 1.3;
    margin-bottom: 0.4em;
    color: #111111;
}

.novel-meta {
    font-size: 0.88em;
    color: #666666;
    margin: 1.2em 0 1.8em 0;
    line-height: 1.6;
}

.meta-item {
    display: inline-block;
    margin: 0 0.8em;
}

.meta-item b {
    color: #333333;
}

.synopsis-box {
    text-align: justify;
    border-top: 1px solid #d5d5d5;
    padding-top: 1.5em;
    margin-top: 1.5em;
}

.synopsis-heading {
    text-align: center;
    font-size: 1.05em;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: #555555;
    margin-bottom: 1em;
}
"""


def clean_text_paragraphs(lines: List[str], clean_blank: bool = True) -> List[str]:
    """Cleans a list of text paragraphs, removing redundant whitespace and blank lines."""
    cleaned = []
    prev_was_blank = False

    for line in lines:
        s = line.strip()
        if s:
            # Normalize fullwidth whitespace
            s = s.replace("\u3000", " ")
            cleaned.append(s)
            prev_was_blank = False
        else:
            if not clean_blank:
                cleaned.append("")
            elif not prev_was_blank:
                # Retain at most one blank line for scene pauses if desired, or skip completely
                # In WTR layout, paragraph margin-bottom already handles spacing, so we skip blank line bloat
                prev_was_blank = True

    return cleaned


def paras_to_qc_html(paras: List[str], css_class: str = "", clean_blank: bool = True) -> str:
    """Converts paragraph strings to clean HTML with scene break detection."""
    out = []
    scene_break_patterns = [
        re.compile(r"^[\*\-_\s]{3,}$"),
        re.compile(r"^[◆◇■□▲△▼▽★☆\s]{3,}$"),
        re.compile(r"^[＝=~～\s]{3,}$"),
    ]

    for p in paras:
        p_clean = p.strip()
        if not p_clean:
            if not clean_blank:
                out.append('<p class="blank"><br/></p>')
            continue

        # Check for scene break marker
        is_break = any(pat.match(p_clean) for pat in scene_break_patterns) or p_clean in ["***", "* * *", "---", "◆◆◆", "◇◇◇", "==="]
        if is_break:
            out.append('<div class="scene-divider">✦ ✦ ✦</div>')
            continue

        out.append(f"<p>{html.escape(p_clean)}</p>")

    inner = "\n".join(out)
    return f'<div class="{css_class}">\n{inner}\n</div>' if css_class else inner


def clean_html_body(raw_html: str) -> str:
    """Cleans HTML content: strips redundant empty <p>, removes blank line bloat, normalizes dividers."""
    # 1. Remove empty <p class="blank"><br/></p> or empty paragraphs
    cleaned = re.sub(r'<p\s+class="blank"[^>]*>\s*(?:<br\s*/?>)?\s*</p>', "", raw_html, flags=re.IGNORECASE)
    cleaned = re.sub(r'<p[^>]*>\s*(?:<br\s*/?>|\s|&nbsp;|&#160;)*\s*</p>', "", cleaned, flags=re.IGNORECASE)

    # 2. Convert raw scene break markers in paragraphs to .scene-divider
    cleaned = re.sub(
        r'<p[^>]*>\s*(?:\*{3,}|\*\s+\*\s+\*|—{3,}|-{3,}|[◆◇]{3,}|={3,})\s*</p>',
        '<div class="scene-divider">✦ ✦ ✦</div>',
        cleaned,
        flags=re.IGNORECASE,
    )

    # 3. Clean consecutive dividers
    cleaned = re.sub(r'(<div class="scene-divider">✦ ✦ ✦</div>\s*){2,}', r'<div class="scene-divider">✦ ✦ ✦</div>\n', cleaned)

    return cleaned.strip()


def build_standard_info_html(meta: Dict[str, Any], lang: str = "en") -> str:
    """Builds a standardized 1-page synopsis and novel overview card."""
    title = meta.get("title", "Untitled Novel")
    writer = meta.get("writer", "Unknown Author")
    url = meta.get("url", "")
    story = meta.get("story", "")
    ncode = meta.get("ncode", "").upper()
    total_episodes = meta.get("total_episodes") or meta.get("chapters_count") or ""
    status = meta.get("status", "")
    target_lang = lang if lang in ("en", "id", "ja") else "en"

    story_paras = story.splitlines() if isinstance(story, str) else []
    story_html = paras_to_qc_html(story_paras, clean_blank=True)

    badges = []
    if ncode:
        badges.append(f'<span class="meta-item"><b>Code:</b> {html.escape(ncode)}</span>')
    if writer:
        badges.append(f'<span class="meta-item"><b>Author:</b> {html.escape(writer)}</span>')
    if url:
        badges.append(f'<span class="meta-item"><b>Source:</b> <a href="{html.escape(url)}">{html.escape(url)}</a></span>')
    if total_episodes:
        badges.append(f'<span class="meta-item"><b>Chapters:</b> {total_episodes}</span>')
    if status:
        badges.append(f'<span class="meta-item"><b>Status:</b> {html.escape(status)}</span>')

    meta_line = "\n".join(badges)

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="{target_lang}" lang="{target_lang}">
<head>
  <meta charset="UTF-8"/>
  <title>{html.escape(title)}</title>
  <link rel="stylesheet" type="text/css" href="style.css"/>
</head>
<body>
  <div class="info-card">
    <h1 class="novel-title">{html.escape(title)}</h1>
    <div class="novel-meta">
      {meta_line}
    </div>
    <div class="synopsis-box">
      <h3 class="synopsis-heading">Synopsis / Sinopsis</h3>
      <div class="synopsis-content">
        {story_html}
      </div>
    </div>
  </div>
</body>
</html>
"""


def build_standard_chapter_html(
    title: str,
    body_paragraphs: List[str],
    volume: str = "",
    preface: Optional[List[str]] = None,
    afterword: Optional[List[str]] = None,
    chapter_num: Optional[int] = None,
    lang: str = "en",
) -> str:
    """Builds a standardized XHTML chapter page with clean typography and volume hierarchy."""
    clean_title = title.strip()
    target_lang = lang if lang in ("en", "id", "ja") else "en"

    # If title has novel name or volume duplicated at the start, clean it
    if volume and clean_title.startswith(volume):
        clean_title = clean_title[len(volume):].lstrip(" -–:：")

    header_parts = ['<div class="chapter-header">']
    if volume:
        header_parts.append(f'  <span class="volume-tag">{html.escape(volume)}</span>')

    # Add chapter numbering prefix if missing
    display_title = clean_title
    if chapter_num and not re.search(r"^(?:ch(?:apter)?\.?|bab|第)\s*\d+", clean_title, re.IGNORECASE):
        display_title = f"Ch. {chapter_num:02d} - {clean_title}" if clean_title else f"Chapter {chapter_num}"

    header_parts.append(f'  <h2 class="chapter-title">{html.escape(display_title)}</h2>')
    header_parts.append("</div>")

    parts = ["\n".join(header_parts)]

    if preface:
        pref_clean = clean_text_paragraphs(preface)
        if pref_clean:
            parts.append(f'<div class="preface">\n<div class="section-label">Preface / 前書き</div>\n{paras_to_qc_html(pref_clean)}\n</div>')

    body_clean = clean_text_paragraphs(body_paragraphs)
    parts.append(paras_to_qc_html(body_clean))

    if afterword:
        aft_clean = clean_text_paragraphs(afterword)
        if aft_clean:
            parts.append(f'<div class="afterword">\n<div class="section-label">Afterword / 後書き</div>\n{paras_to_qc_html(aft_clean)}\n</div>')

    inner_content = "\n\n".join(parts)

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="{target_lang}" lang="{target_lang}">
<head>
  <meta charset="UTF-8"/>
  <title>{html.escape(display_title)}</title>
  <link rel="stylesheet" type="text/css" href="style.css"/>
</head>
<body>
{inner_content}
</body>
</html>
"""


def standardize_epub_file(epub_path: pathlib.Path, output_path: Optional[pathlib.Path] = None) -> Tuple[pathlib.Path, Dict[str, Any]]:
    """Inspects and standardizes an existing EPUB archive in-place or to a new path.
    Standardizes:
    - OEBPS/style.css with WTR typography
    - OEBPS/info.xhtml with clean 1-page synopsis card
    - All chapter files (strips blank line bloat, cleans headers & scene breaks)
    """
    epub_path = pathlib.Path(epub_path)
    if not epub_path.is_file():
        raise FileNotFoundError(f"EPUB file not found: {epub_path}")

    dest = pathlib.Path(output_path) if output_path else epub_path
    temp_dir = pathlib.Path(tempfile.mkdtemp(prefix="ncode_qc_"))

    stats = {
        "chapters_processed": 0,
        "blank_tags_removed": 0,
        "dividers_standardized": 0,
        "info_standardized": False,
        "style_updated": True,
    }

    try:
        # 1. Extract existing EPUB
        with zipfile.ZipFile(epub_path, "r") as z:
            z.extractall(temp_dir)

        # 2. Locate OEBPS or root content folder
        oebps_dir = temp_dir / "OEBPS"
        if not oebps_dir.is_dir():
            oebps_dir = temp_dir

        # 3. Update style.css
        style_path = oebps_dir / "style.css"
        style_path.write_text(WTR_STANDARD_CSS, encoding="utf-8")

        # 4. Standardize info.xhtml (1-page synopsis card)
        info_path = oebps_dir / "info.xhtml"
        opf_path = oebps_dir / "content.opf"
        opf_text = opf_path.read_text(encoding="utf-8", errors="ignore") if opf_path.is_file() else ""

        # Extract authoritative metadata from content.opf if available
        opf_title_m = re.search(r"<dc:title[^>]*>(.*?)</dc:title>", opf_text, re.IGNORECASE | re.DOTALL)
        opf_writer_m = re.search(r"<dc:creator[^>]*>(.*?)</dc:creator>", opf_text, re.IGNORECASE | re.DOTALL)
        opf_url_m = re.search(r"<dc:source[^>]*>(.*?)</dc:source>", opf_text, re.IGNORECASE | re.DOTALL)
        opf_desc_m = re.search(r"<dc:description[^>]*>(.*?)</dc:description>", opf_text, re.IGNORECASE | re.DOTALL)

        if info_path.is_file():
            raw_info = info_path.read_text(encoding="utf-8", errors="ignore")

            # Title
            title = ""
            if opf_title_m:
                title = html.unescape(opf_title_m.group(1).strip())
            if not title:
                title_m = re.search(r"<h1[^>]*>(.*?)</h1>", raw_info, re.IGNORECASE | re.DOTALL)
                title = html.unescape(title_m.group(1).strip()) if title_m else epub_path.stem

            # Writer
            writer = ""
            if opf_writer_m:
                writer = html.unescape(opf_writer_m.group(1).strip())
            if not writer:
                writer_m = (
                    re.search(r"<b>Author:</b>\s*([^<]+)", raw_info, re.IGNORECASE)
                    or re.search(r"<p>([^<]+?)</p>\s*<p>(?:https?://[^<]+?)</p>", raw_info, re.IGNORECASE)
                )
                writer = html.unescape(writer_m.group(1).strip()) if writer_m else "Unknown"

            # URL / Source
            url = ""
            if opf_url_m:
                url = html.unescape(opf_url_m.group(1).strip())
            if not url:
                url_m = re.search(r"(https?://[^\s\"'<>]+)", raw_info, re.IGNORECASE)
                url = url_m.group(1).strip() if url_m else ""

            # Story / Synopsis
            story_content = ""
            if opf_desc_m and opf_desc_m.group(1).strip():
                story_content = html.unescape(opf_desc_m.group(1).strip())
            if not story_content:
                story_m = (
                    re.search(r'<div class="(?:synopsis-content|synopsis-box|story)">(.*?)</div>', raw_info, re.IGNORECASE | re.DOTALL)
                )
                if story_m:
                    p_matches = re.findall(r"<p[^>]*>(.*?)</p>", story_m.group(1), re.IGNORECASE | re.DOTALL)
                    clean_lines = [html.unescape(p.strip()) for p in p_matches if p.strip() and not p.strip().startswith("<br")]
                    story_content = "\n".join(clean_lines)

            lang_m = re.search(r"<dc:language[^>]*>(.*?)</dc:language>", opf_text, re.IGNORECASE)
            epub_lang = lang_m.group(1).strip() if lang_m else ""
            # Auto-detect if title or filename indicates translated EN / ID
            if not epub_lang or epub_lang == "ja":
                if "[en]" in title.lower() or "[en]" in epub_path.name.lower():
                    epub_lang = "en"
                elif "[id]" in title.lower() or "[id]" in epub_path.name.lower():
                    epub_lang = "id"
                else:
                    epub_lang = epub_lang or "ja"

            if epub_lang != "ja":
                opf_text = re.sub(r"<dc:language[^>]*>.*?</dc:language>", f"<dc:language>{epub_lang}</dc:language>", opf_text)

            meta = {
                "title": title,
                "writer": writer,
                "url": url,
                "story": story_content,
            }
            new_info_html = build_standard_info_html(meta, lang=epub_lang)
            info_path.write_text(new_info_html, encoding="utf-8")
            stats["info_standardized"] = True

        # 4b. Ensure retro B&W cover.jpg exists (matching syosetu_to_epub.user.js & Calibre)
        has_cover = any((oebps_dir / f).is_file() for f in ("cover.jpg", "cover.jpeg", "cover.png"))
        if not has_cover and (oebps_dir / "content.opf").is_file():
            try:
                from .crawler import _generate_retro_cover_jpg
                lang_m = re.search(r"<dc:language[^>]*>(.*?)</dc:language>", opf_text, re.IGNORECASE)
                cov_lang = lang_m.group(1).strip() if lang_m else "ja"
                tag_label = f"{cov_lang.upper()} EDITION" if cov_lang != "ja" else "ORIGINAL EDITION"
                cov_bytes = _generate_retro_cover_jpg(title, writer, tag=tag_label)
                (oebps_dir / "cover.jpg").write_bytes(cov_bytes)

                if 'id="cover-image"' not in opf_text:
                    opf_text = re.sub(
                        r"<manifest>",
                        '<manifest>\n<item id="cover-image" href="cover.jpg" media-type="image/jpeg" properties="cover-image"/>\n<item id="cover-page" href="cover.xhtml" media-type="application/xhtml+xml"/>',
                        opf_text,
                        count=1,
                    )
                    if '<meta name="cover"' not in opf_text:
                        opf_text = re.sub(r"<metadata([^>]*)>", r'<metadata\1>\n<meta name="cover" content="cover-image"/>', opf_text, count=1)
                    if '<itemref idref="cover-page"' not in opf_text:
                        opf_text = re.sub(r"<spine([^>]*)>", r'<spine\1>\n<itemref idref="cover-page"/>', opf_text, count=1)
                    opf_path.write_text(opf_text, encoding="utf-8")

                cov_page = (
                    '<?xml version="1.0" encoding="utf-8"?>\n'
                    '<!DOCTYPE html>\n'
                    '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">\n'
                    '<head><title>Cover</title><style>body,html{margin:0;padding:0;height:100%;text-align:center;background:#111114;}img{width:100%;height:100%;max-width:100%;object-fit:contain;}</style></head>\n'
                    '<body><div><img src="cover.jpg" alt="Cover" /></div></body>\n</html>'
                )
                (oebps_dir / "cover.xhtml").write_text(cov_page, encoding="utf-8")
            except Exception:
                pass

        # 5. Process all chapter xhtml files
        for ch_file in sorted(oebps_dir.glob("ch*.xhtml")):
            raw_ch = ch_file.read_text(encoding="utf-8", errors="ignore")

            # Count blank lines before cleaning
            blanks_before = len(re.findall(r'class="blank"|<p[^>]*>\s*(?:<br\s*/?>)?\s*</p>', raw_ch, re.IGNORECASE))
            stats["blank_tags_removed"] += blanks_before

            cleaned_body = clean_html_body(raw_ch)

            # Ensure chapter header uses standardized class
            cleaned_body = re.sub(
                r'<h2>(.*?)</h2>',
                r'<div class="chapter-header"><h2 class="chapter-title">\1</h2></div>',
                cleaned_body,
                count=1,
            )

            ch_file.write_text(cleaned_body, encoding="utf-8")
            stats["chapters_processed"] += 1

        # 5b. Save updated content.opf
        (oebps_dir / "content.opf").write_text(opf_text, encoding="utf-8")

        # 6. Repack into valid EPUB3
        temp_epub = temp_dir.parent / f"qc_{epub_path.name}"
        with zipfile.ZipFile(temp_epub, "w") as zout:
            # First write mimetype uncompressed (EPUB spec)
            mimetype_file = temp_dir / "mimetype"
            if mimetype_file.is_file():
                zout.write(mimetype_file, "mimetype", compress_type=zipfile.ZIP_STORED)

            for root, dirs, files in os.walk(temp_dir):
                for f in files:
                    full_p = pathlib.Path(root) / f
                    rel_p = full_p.relative_to(temp_dir)
                    if str(rel_p) == "mimetype":
                        continue
                    zout.write(full_p, str(rel_p).replace("\\", "/"), compress_type=zipfile.ZIP_DEFLATED)

        # Move to destination
        shutil.move(str(temp_epub), str(dest))
        return dest, stats

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def run_batch_qc(folder_path: pathlib.Path) -> List[Dict[str, Any]]:
    """Runs Quality Control standardization over all EPUBs in a directory."""
    folder = pathlib.Path(folder_path)
    results = []
    for epub in sorted(folder.glob("*.epub")):
        try:
            _, stats = standardize_epub_file(epub)
            results.append({"file": epub.name, "ok": True, "stats": stats})
        except Exception as e:
            results.append({"file": epub.name, "ok": False, "error": str(e)})
    return results


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="ncode_pipe Quality Control (QC) & EPUB Standardizer")
    parser.add_argument("path", nargs="?", default="downloads", help="Path to EPUB file or downloads folder")
    parser.add_argument("--batch", action="store_true", help="Process all EPUBs in the directory")

    args = parser.parse_args()
    target = pathlib.Path(args.path)

    if target.is_dir() or args.batch:
        folder = target if target.is_dir() else pathlib.Path("downloads")
        print(f"[*] Running batch EPUB Quality Control on: {folder}")
        res = run_batch_qc(folder)
        for r in res:
            if r["ok"]:
                s = r["stats"]
                print(f" [OK] {r['file']} -> {s['chapters_processed']} ch, {s['blank_tags_removed']} blank tags cleaned")
            else:
                print(f" [ERROR] {r['file']}: {r.get('error')}")
    elif target.is_file():
        print(f"[*] Standardizing EPUB: {target.name}...")
        _, stats = standardize_epub_file(target)
        print(f" [*] Done! Processed {stats['chapters_processed']} chapters. Removed {stats['blank_tags_removed']} blank tags.")
    else:
        print(f"[ERROR] Target path not found: {target}")
