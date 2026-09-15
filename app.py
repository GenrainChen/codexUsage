"""Start the desktop note or print a metadata-only usage report."""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main() -> int:
    parser = argparse.ArgumentParser(description='Codex 用量便签')
    parser.add_argument('--codex-home', type=Path,
                        default=Path(os.environ.get('CODEX_HOME', Path.home() / '.codex')))
    parser.add_argument('--report', action='store_true', help='输出本地计量摘要 JSON，不开启窗口')
    parser.add_argument('--date', help='北京时间报告日期 YYYY-MM-DD；省略为全部历史')
    args = parser.parse_args()
    from usage_note.pricing import PriceBook
    from usage_note.reader import UsageReader

    reader = UsageReader(args.codex_home)
    prices = PriceBook(ROOT / 'prices.json', ROOT / '.local' / 'price-overrides.json')
    if args.report:
        report = reader.scan()
        report['timezone'] = str(reader.tz)
        if args.date:
            report['days'] = {args.date: report['days'].get(args.date, {})}
            report['output_days'] = {day: row for day, row in report['output_days'].items()
                                     if day == args.date}
            report['hours'] = {hour: models for hour, models in report['hours'].items()
                               if hour[:10] == args.date}
        for grouping in ('days', 'hours'):
            for models in report[grouping].values():
                for model, tokens in models.items():
                    tokens['quote'] = prices.quote(model, tokens)
        report['pricing_warnings'] = prices.warnings
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        return 0

    import tkinter as tk
    from usage_note.ui import UsageNote
    from usage_note.branding import set_app_identity

    set_app_identity()
    root = tk.Tk()
    root.title('Codex 用量便签')
    UsageNote(root, reader, prices, ROOT / '.local' / 'settings.json')
    root.mainloop()
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception:
        log_dir = ROOT / '.local'
        log_dir.mkdir(exist_ok=True)
        detail = traceback.format_exc()
        (log_dir / 'app-error.log').write_text(detail, encoding='utf-8')
        if sys.stderr is not None:
            print(detail, file=sys.stderr)
        else:
            import tkinter as tk
            from tkinter import messagebox
            error_root = tk.Tk()
            error_root.withdraw()
            messagebox.showerror('Codex 用量便签', '启动失败，错误详情已保存：\n' + str(log_dir / 'app-error.log'))
            error_root.destroy()
        raise SystemExit(1)
