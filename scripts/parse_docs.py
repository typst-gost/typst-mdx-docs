#!/usr/bin/env python3

import argparse
import concurrent.futures
import json
import shutil
import sys
import traceback
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from bs4 import BeautifulSoup, Tag
from loguru import logger
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn, TimeElapsedColumn

from parser.html_to_mdx import extract_page_metadata, html_to_mdx
from utils import ensure_directories

DEFAULT_DIST_SOURCE = Path("build/dist")
DEFAULT_OUTPUT_DIR = Path("build/mdx")
DEFAULT_ASSETS_OUT_DIR = DEFAULT_OUTPUT_DIR / "public" / "docs-assets" / "images"
DEFAULT_ASSETS_PUBLIC_PATH = "/docs-assets/images"


def resolve_site_root(source_dir: Path) -> Path:
    if (source_dir / "index.html").exists():
        return source_dir
    if (source_dir / "site" / "index.html").exists():
        return source_dir / "site"
    raise FileNotFoundError(
        f"Could not find index.html in '{source_dir}' or '{source_dir / 'site'}'. "
        "Run `uv run scripts/build_docs.py` first."
    )


def parse_nav_ul(ul_tag: Tag) -> list[dict]:
    items: list[dict] = []
    for li in ul_tag.find_all("li", recursive=False):
        classes = li.get("class") or []
        if "category" in classes:
            items.append({"type": "category", "title": li.get_text(strip=True)})
            continue

        a_tag = li.find("a", recursive=False)
        if not isinstance(a_tag, Tag):
            continue

        href = str(a_tag.get("href", "/")).strip("/")
        title = a_tag.get_text(strip=True)
        child_ul = li.find("ul", recursive=False)
        children = parse_nav_ul(child_ul) if isinstance(child_ul, Tag) else []

        items.append({
            "type": "page",
            "title": title,
            "route": href,
            "children": children,
        })
    return items


def extract_navigation_tree(index_html: Path) -> list[dict]:
    soup = BeautifulSoup(index_html.read_text(encoding="utf-8"), "html.parser")
    nav = soup.find("nav", class_="folding")
    if not isinstance(nav, Tag):
        logger.warning("Could not find <nav class='folding'>, falling back to filesystem scan")
        return []

    for ul in nav.find_all("ul", recursive=False):
        if ul.get("id") == "search-results":
            continue
        return parse_nav_ul(ul)
    return []


def collect_pages_and_meta(
    nav_items: list[dict],
    site_root: Path,
    pages_out: list[dict],
    meta_out: list[dict],
) -> list[str]:
    ordered_slugs: list[str] = []

    for item in nav_items:
        if item["type"] == "category":
            ordered_slugs.append(f"---{item['title']}---")
            continue

        route = item["route"]
        children = item["children"]
        has_children = bool(children)

        html_path = site_root / route / "index.html" if route else site_root / "index.html"
        if not html_path.exists():
            logger.warning(f"HTML file missing for route '/{route}' at {html_path}")
            continue

        slug = route.split("/")[-1] if route else "index"
        ordered_slugs.append(slug)

        pages_out.append({
            "route": route,
            "html_path": html_path,
            "has_children": has_children,
            "nav_title": item["title"],
        })

        if has_children:
            child_slugs = collect_pages_and_meta(children, site_root, pages_out, meta_out)
            _, description = extract_page_metadata(html_path.read_text(encoding="utf-8"))
            meta_out.append({
                "route": route,
                "title": item["title"],
                "description": description,
                "pages": child_slugs,
                "root": False,
            })

    return ordered_slugs


def write_meta_json(output_dir: Path, meta: dict) -> None:
    folder = output_dir / meta["route"] if meta["route"] else output_dir
    folder.mkdir(parents=True, exist_ok=True)
    meta_path = folder / "meta.json"

    data: dict = {
        "title": meta["title"],
        "description": meta["description"],
        "pages": meta["pages"],
    }
    if meta.get("root"):
        data["root"] = True

    meta_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def convert_single_page(
    page: dict,
    output_dir: Path,
    link_prefix: str,
    math_format: str,
    image_public_path: str,
) -> tuple[str, str, str | None]:
    route = page["route"]
    html_path: Path = page["html_path"]

    try:
        html_content = html_path.read_text(encoding="utf-8")
        title, description = extract_page_metadata(html_content)
        body_mdx = html_to_mdx(
            html_content,
            link_prefix=link_prefix,
            math_format=math_format,
            image_public_path=image_public_path,
        )

        imports: list[str] = []
        if "<TypstPreview" in body_mdx:
            imports.append("import { TypstPreview } from '@/components/typst/preview';")
        if "<Callout" in body_mdx:
            imports.append("import { Callout } from 'fumadocs-ui/components/callout';")

        imports_block = ("\n" + "\n".join(imports) + "\n") if imports else ""

        frontmatter = (
            "---\n"
            f"title: {json.dumps(title, ensure_ascii=False)}\n"
            f"description: {json.dumps(description, ensure_ascii=False)}\n"
            "---\n"
        )

        mdx_content = f"{frontmatter}{imports_block}\n{body_mdx}\n"

        if not route:
            file_path = output_dir / "index.mdx"
        elif page["has_children"]:
            folder = output_dir / route
            folder.mkdir(parents=True, exist_ok=True)
            file_path = folder / "index.mdx"
        else:
            target = output_dir / route
            target.parent.mkdir(parents=True, exist_ok=True)
            file_path = Path(f"{target}.mdx")

        file_path.write_text(mdx_content, encoding="utf-8")
        return "OK", route or "index", None
    except Exception:
        return "ERROR", route or "index", traceback.format_exc()


def fallback_scan_pages(site_root: Path) -> list[dict]:
    pages: list[dict] = []
    for html_file in sorted(site_root.rglob("index.html")):
        rel = html_file.relative_to(site_root).parent
        route = "" if str(rel) == "." else rel.as_posix()
        has_children = any(
            d.is_dir() and (d / "index.html").exists() for d in html_file.parent.iterdir()
        )
        pages.append({
            "route": route,
            "html_path": html_file,
            "has_children": has_children,
            "nav_title": None,
        })
    return pages


def copy_images(site_root: Path, target_dir: Path) -> None:
    src_images = site_root / "assets" / "images"
    if not src_images.exists():
        src_images = site_root.parent / "assets" / "images"

    if not src_images.exists():
        logger.warning(f"Images directory not found at {src_images}")
        return

    logger.info(f"Copying images from {src_images.resolve()} to {target_dir.resolve()}...")
    if target_dir.exists():
        shutil.rmtree(target_dir)

    target_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src_images, target_dir)
    logger.success(f"Copied {len(list(target_dir.iterdir()))} images to {target_dir.resolve()}")


def parse_docs(
    source_dir: Path = DEFAULT_DIST_SOURCE,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    link_prefix: str = "/",
    math_format: str = "latex",
    assets_out_dir: Path | None = None,
    assets_public_path: str = DEFAULT_ASSETS_PUBLIC_PATH,
) -> int:
    site_root = resolve_site_root(source_dir)
    logger.info(f"Reading HTML docs from {site_root.resolve()}")

    if output_dir.exists():
        shutil.rmtree(output_dir)
    ensure_directories([output_dir])

    actual_assets_out_dir = assets_out_dir if assets_out_dir is not None else (output_dir / "public" / "docs-assets" / "images")
    copy_images(site_root, actual_assets_out_dir)

    nav_tree = extract_navigation_tree(site_root / "index.html")
    pages_to_process: list[dict] = []
    meta_files: list[dict] = []

    if nav_tree:
        root_pages_order = collect_pages_and_meta(nav_tree, site_root, pages_to_process, meta_files)
        _, root_desc = extract_page_metadata((site_root / "index.html").read_text(encoding="utf-8"))
        meta_files.append({
            "route": "",
            "title": "Typst Documentation",
            "description": root_desc or "Official Typst Documentation",
            "pages": root_pages_order,
            "root": True,
        })

        known_routes = {p["route"] for p in pages_to_process}
        for scanned in fallback_scan_pages(site_root):
            if scanned["route"] not in known_routes:
                pages_to_process.append(scanned)
    else:
        pages_to_process = fallback_scan_pages(site_root)

    if not pages_to_process:
        logger.error(f"No HTML pages found in {site_root}")
        return 1

    logger.info(f"Found {len(pages_to_process)} pages and {len(meta_files)} meta.json files")

    for meta in meta_files:
        write_meta_json(output_dir, meta)

    errors = 0
    with Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}"),
        BarColumn(bar_width=35),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        TextColumn("[dim]{task.fields[current]}"),
        transient=True,
    ) as progress:
        task = progress.add_task("Converting HTML to MDX", total=len(pages_to_process), current="")

        with concurrent.futures.ProcessPoolExecutor() as executor:
            futures = [
                executor.submit(
                    convert_single_page,
                    page,
                    output_dir,
                    link_prefix,
                    math_format,
                    assets_public_path,
                )
                for page in pages_to_process
            ]
            for future in concurrent.futures.as_completed(futures):
                status, route, err = future.result()
                progress.update(task, advance=1, current=route)
                if status == "ERROR":
                    errors += 1
                    progress.console.print(f"[red]Failed '{route}':[/red]\n{err}")

    if errors:
        logger.warning(f"Completed with {errors} error(s)")
        return 1

    logger.success(f"Successfully built {len(pages_to_process)} MDX pages into {output_dir.resolve()}")
    return 0


def parse_args():
    parser = argparse.ArgumentParser(description="Typst HTML Docs (docit) to MDX Converter")
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=DEFAULT_DIST_SOURCE,
        help="Path to compiled docit output (default: build/dist)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Output directory for generated MDX files (default: build/mdx)",
    )
    parser.add_argument(
        "--link-prefix",
        type=str,
        default="/",
        help="Prefix for internal doc links (default: '/', use '' for no leading slash)",
    )
    parser.add_argument(
        "--math-format",
        choices=["latex", "mathml"],
        default="latex",
        help="Format for math expressions (default: latex)",
    )
    parser.add_argument(
        "--assets-out-dir",
        type=Path,
        default=None,
        help="Directory to copy images into (default: <output-dir>/public/docs-assets/images)",
    )
    parser.add_argument(
        "--assets-public-path",
        type=str,
        default=DEFAULT_ASSETS_PUBLIC_PATH,
        help="Public URL path prefix to images in MDX (default: /docs-assets/images)",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    try:
        code = parse_docs(
            source_dir=args.source_dir,
            output_dir=args.output_dir,
            link_prefix=args.link_prefix,
            math_format=args.math_format,
            assets_out_dir=args.assets_out_dir,
            assets_public_path=args.assets_public_path,
        )
        exit(code)
    except FileNotFoundError as exc:
        logger.error(str(exc))
        exit(1)


if __name__ == "__main__":
    main()