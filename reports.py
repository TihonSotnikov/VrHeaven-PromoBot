"""Тексты табличных отчётов — HTML для нативных таблиц (Rich Messages).

Экраны с таблицами рендерятся через render(..., rich=True): новые
сообщения уходят через sendRichMessage, обновление окна — через
параметр rich_message метода editMessageText.
"""

from zoneinfo import ZoneInfo

from db import Database
from utils import esc_html, fmt_dt, fmt_money, fmt_num, table_html

MAX_TABLE_ROWS = 25


async def admin_summary_text(db: Database) -> str:
    """Сводка текущего периода по всем партнёрам."""
    rows = await db.unpaid_summary()
    rows = [r for r in rows if r["is_active"] or r["orders_count"] > 0]
    head = "<p><b>Сводка за текущий период</b></p>"
    if not rows:
        return head + "<p>Партнёры пока не подключены</p>"

    t_rows = []
    total_n, total_turnover, total_commission = 0, 0.0, 0.0
    for r in rows:
        t_rows.append([
            r["promo_code"],
            r["orders_count"],
            fmt_num(r["turnover"]),
            fmt_num(r["commission_sum"]),
        ])
        total_n += r["orders_count"]
        total_turnover += r["turnover"]
        total_commission += r["commission_sum"]
    t_rows.append(["Итого", total_n, fmt_num(total_turnover), fmt_num(total_commission)])

    tbl = table_html(["Код", "Заказы", "Оборот", "К выплате"], t_rows)
    return head + tbl + f"<p><b>Итого к выплате: {fmt_money(total_commission)}</b></p>"


async def partner_period_text(
    db: Database, partner, tz: ZoneInfo, title: str = "Текущий период"
) -> str:
    """Отчёт партнёра по невыплаченным заказам."""
    orders = await db.unpaid_orders(partner["id"])
    head = f"<p><b>{esc_html(title)} · {esc_html(partner['promo_code'])}</b></p>"
    if not orders:
        return head + "<p>Заказов в текущем периоде пока нет</p>"

    shown = orders[-MAX_TABLE_ROWS:]
    t_rows = [
        [fmt_dt(o["created_at"], tz, "%d.%m %H:%M"), fmt_num(o["amount"]), fmt_num(o["commission"])]
        for o in shown
    ]
    tbl = table_html(["Дата", "Сумма", "Комиссия"], t_rows)

    note = ""
    if len(orders) > MAX_TABLE_ROWS:
        note = f"<p>Показаны последние {MAX_TABLE_ROWS} из {len(orders)} заказов</p>"

    turnover = sum(o["amount"] for o in orders)
    commission = sum(o["commission"] for o in orders)
    return (
        head
        + tbl
        + note
        + f"<p>Заказов: {len(orders)}<br>"
        + f"Оборот: {fmt_money(turnover)}<br>"
        + f"<b>К выплате: {fmt_money(commission)}</b></p>"
    )


def payout_history_text(payouts, tz: ZoneInfo, promo_code: str) -> str:
    head = f"<p><b>История выплат · {esc_html(promo_code)}</b></p>"
    if not payouts:
        return head + "<p>Выплат пока не было</p>"
    tbl = table_html(
        ["Дата", "Сумма", "Заказы"],
        [[fmt_dt(p["created_at"], tz, "%d.%m.%Y"), fmt_num(p["amount"]), p["orders_count"]]
         for p in payouts],
    )
    return head + tbl
