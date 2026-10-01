"""Import once; all pages read catalog automatically. No HTML editing required."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import video_catalog as vc


def main():
    parser = argparse.ArgumentParser(description="导入或校验本地视频库")
    parser.add_argument("folder", nargs="?", help="TED/ 下的相对目录")
    parser.add_argument("--metadata", type=Path, help="补充元数据 JSON 文件")
    parser.add_argument("--id", help="编辑已有固定 ID，移动目录时仍保留旧别名")
    parser.add_argument("--check", action="store_true", help="检查全部目录，不写入数据")
    args = parser.parse_args()
    try:
        if args.check:
            result = vc.catalog()
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 1 if result["issues"] else 0
        if not args.folder:
            parser.error("导入时必须提供视频目录")
        fields = json.loads(args.metadata.read_text(encoding="utf-8-sig")) if args.metadata else {}
        video = vc.save_video(args.folder, fields, args.id)
        print(f"已导入：{video['title']}\nID：{video['id']}")
        return 0
    except (OSError, ValueError) as exc:
        print(f"导入失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    # Windows redirected output can default to GBK; metadata contains arbitrary Unicode.
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
