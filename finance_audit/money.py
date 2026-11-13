from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


def to_cents(value):
    if value is None or str(value).strip() == "":
        return None
    text = str(value).strip().replace(",", "").replace("¥", "").replace("￥", "")
    try:
        amount = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError("金额不是有效数字") from exc
    if not amount.is_finite():
        raise ValueError("金额不是有效数字")
    if amount != amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP):
        raise ValueError("金额最多保留两位小数")
    return int(amount * 100)


def yuan(cents):
    if cents is None:
        return None
    return f"{Decimal(cents) / 100:.2f}"
