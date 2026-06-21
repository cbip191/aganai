from config import DISCOUNT_RATE, PROJECTION_YEARS, TERMINAL_GROWTH_RATE

MIN_GROWTH = -0.20
MAX_GROWTH = 0.30
MIN_DATA_YEARS_SIMPLE = 3


def estimate_growth_rate(fcf_history):
    positive = [v for v in fcf_history if v and v > 0]
    if len(positive) < MIN_DATA_YEARS_SIMPLE:
        return TERMINAL_GROWTH_RATE
    first, last = positive[0], positive[-1]
    if first <= 0:
        return TERMINAL_GROWTH_RATE
    years = len(positive) - 1
    if years <= 0:
        return TERMINAL_GROWTH_RATE
    cagr = (last / first) ** (1 / years) - 1
    return max(MIN_GROWTH, min(MAX_GROWTH, cagr))


def project_fcf_simple(current_fcf, growth_rate, years):
    projected = []
    for y in range(1, years + 1):
        projected.append(current_fcf * (1 + growth_rate) ** y)
    return projected


def project_fcf_investment_adjusted(financials_by_year, sector_growth, company_analysis, years):
    sorted_years = sorted(financials_by_year.keys())
    latest = financials_by_year[sorted_years[-1]]
    current_fcf = latest.get("fcf")
    if not current_fcf or current_fcf <= 0:
        return None

    base_growth = sector_growth.get("growth_rate", TERMINAL_GROWTH_RATE)
    lag = company_analysis.get("investment_lag", 2)
    avg_roi = company_analysis.get("avg_roi", 0)
    sector_roi = company_analysis.get("sector_avg_roi", 0)

    if sector_roi and sector_roi > 0:
        effectiveness = avg_roi / sector_roi
    else:
        effectiveness = 1.0
    effectiveness = max(0.5, min(2.0, effectiveness))

    recent_years = sorted_years[-3:]
    recent_inv = []
    for y in recent_years:
        inv = financials_by_year[y].get("total_investment")
        if inv and inv > 0:
            recent_inv.append(inv)

    older_years = sorted_years[-6:-3] if len(sorted_years) >= 6 else sorted_years[:3]
    older_inv = []
    for y in older_years:
        inv = financials_by_year[y].get("total_investment")
        if inv and inv > 0:
            older_inv.append(inv)

    if recent_inv and older_inv:
        avg_recent = sum(recent_inv) / len(recent_inv)
        avg_older = sum(older_inv) / len(older_inv)
        if avg_older > 0:
            inv_growth_factor = avg_recent / avg_older
        else:
            inv_growth_factor = 1.0
    else:
        inv_growth_factor = 1.0
    inv_growth_factor = max(0.7, min(1.5, inv_growth_factor))

    projected = []
    for y in range(1, years + 1):
        if y <= lag:
            year_growth = base_growth * effectiveness
        else:
            year_growth = base_growth * effectiveness * inv_growth_factor
        year_growth = max(MIN_GROWTH, min(MAX_GROWTH, year_growth))
        if projected:
            projected.append(projected[-1] * (1 + year_growth))
        else:
            projected.append(current_fcf * (1 + year_growth))

    return projected


def discount_cash_flows(cash_flows, discount_rate):
    total = 0
    for i, cf in enumerate(cash_flows):
        total += cf / (1 + discount_rate) ** (i + 1)
    return total


def calculate_terminal_value(final_fcf, terminal_growth, discount_rate, years):
    if discount_rate <= terminal_growth:
        return 0
    tv = final_fcf * (1 + terminal_growth) / (discount_rate - terminal_growth)
    return tv / (1 + discount_rate) ** years


def calculate_intrinsic_value(financials_by_year, sector_growth=None, company_analysis=None,
                               discount_rate=DISCOUNT_RATE, terminal_growth=TERMINAL_GROWTH_RATE,
                               projection_years=PROJECTION_YEARS):
    sorted_years = sorted(financials_by_year.keys())
    if len(sorted_years) < MIN_DATA_YEARS_SIMPLE:
        return None

    latest = financials_by_year[sorted_years[-1]]
    current_fcf = latest.get("fcf")
    if not current_fcf or current_fcf <= 0:
        return None

    debt = latest.get("debt") or 0
    cash = latest.get("cash") or 0
    shares = latest.get("shares")
    if not shares or shares <= 0:
        return None

    use_investment_model = (
        sector_growth is not None
        and company_analysis is not None
        and len(sorted_years) >= 7
        and company_analysis.get("avg_roi") is not None
    )

    if use_investment_model:
        projected = project_fcf_investment_adjusted(
            financials_by_year, sector_growth, company_analysis, projection_years
        )
        if projected is None:
            use_investment_model = False

    if not use_investment_model:
        fcf_history = [financials_by_year[y].get("fcf") for y in sorted_years]
        growth_rate = estimate_growth_rate(fcf_history)
        projected = project_fcf_simple(current_fcf, growth_rate, projection_years)
    else:
        fcf_history = [financials_by_year[y].get("fcf") for y in sorted_years]
        growth_rate = estimate_growth_rate(fcf_history)

    pv_fcf = discount_cash_flows(projected, discount_rate)
    tv = calculate_terminal_value(projected[-1], terminal_growth, discount_rate, projection_years)
    intrinsic_value = pv_fcf + tv - debt + cash

    return {
        "intrinsic_value": intrinsic_value,
        "per_share_value": intrinsic_value / shares,
        "growth_rate": growth_rate,
        "projected_fcfs": projected,
        "pv_fcf": pv_fcf,
        "terminal_value": tv,
        "debt": debt,
        "cash": cash,
        "shares": shares,
        "years_of_data": len(sorted_years),
        "model_used": "investment-adjusted" if use_investment_model else "simple",
    }
