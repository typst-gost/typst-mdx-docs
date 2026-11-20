import os
import sys
import argparse


def normalize_ignore(p: str) -> str:
    # убираем слэши по краям и нормализуем разделители
    return os.path.normpath(p).strip(os.sep)


def build_ignore_predicates(ignore_dirs, ignore_files):
    ignore_dirs = {normalize_ignore(d) for d in (ignore_dirs or [])}
    ignore_files = {normalize_ignore(f) for f in (ignore_files or [])}

    def relpath(path: str) -> str:
        # путь относительно корня поиска
        return os.path.relpath(path, start=ROOT_FOLDER)

    def dir_ignored(dirpath: str) -> bool:
        rel = relpath(dirpath)
        name = os.path.basename(rel)
        # совпадение по имени ИЛИ по относительному пути
        return name in ignore_dirs or rel in ignore_dirs

    def file_ignored(filepath: str) -> bool:
        rel = relpath(filepath)
        name = os.path.basename(rel)
        return name in ignore_files or rel in ignore_files

    return dir_ignored, file_ignored


ROOT_FOLDER = ""  # будет задан в main()


def find_top_files(folder: str, limit: int, ignore_dirs, ignore_files):
    global ROOT_FOLDER
    ROOT_FOLDER = folder

    dir_ignored, file_ignored = build_ignore_predicates(ignore_dirs, ignore_files)
    results = []

    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if not dir_ignored(os.path.join(root, d))]

        for name in files:
            path = os.path.join(root, name)

            if file_ignored(path):
                continue

            try:
                size = os.path.getsize(path)
            except OSError:
                continue

            results.append((path, size))

    results.sort(key=lambda item: item[1], reverse=True)
    return results[:limit]


def main():
    parser = argparse.ArgumentParser(
        description="Найти топ-N самых больших файлов в папке и поддиректориях."
    )
    parser.add_argument("folder", help="Путь к папке для поиска")
    parser.add_argument("-n", "--limit", type=int, default=5)
    parser.add_argument("--ignore-dir", action="append", default=[])
    parser.add_argument("--ignore-file", action="append", default=[])

    args = parser.parse_args()

    if not os.path.isdir(args.folder):
        print(f"Папка не найдена: {args.folder}")
        sys.exit(1)

    top_files = find_top_files(
        args.folder,
        args.limit,
        args.ignore_dir,
        args.ignore_file,
    )

    if not top_files:
        print("Файлы не найдены (или все исключены фильтрами).")
        return

    print(f"Топ {len(top_files)} самых больших файлов:")
    for i, (path, size) in enumerate(top_files, start=1):
        print(f"{i}. {path} — {size} байт")


if __name__ == "__main__":
    main()