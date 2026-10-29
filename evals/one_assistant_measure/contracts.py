"""Long synthetic supply agreements for the heavy fan-out chat (WS-67 OA-9f).

``fan_contracts`` needs source material that is long enough to flood the main
chat (``one_assistant_context.md`` section 6.3). The text is built here from
fixed clauses, so the repo holds no large file and every call returns the same
bytes. Each contract is about 40,000 characters. Two facts differ between
vendors: the payment term, after character 20,000, and the late-delivery
penalty, after character 30,000. No other clause holds a
"percent" figure or a day count, so the answer can quote only those two.

Every name and number here is synthetic.
"""
from __future__ import annotations

from collections.abc import Iterator

#: Each vendor: ``(name, host, payment days, penalty percent a week)``. All
#: values differ, and no clause of the boilerplate holds one of them.
CONTRACTS = (
    ("Bharat Castings", "www.bharatcastings-foundry.com", 21, "0.5"),
    ("Meridian Plastics", "www.meridianplastics-polymers.com", 45, "1.5"),
    ("Sunrise Electricals", "www.sunriseelectricals-india.com", 60, "2"),
    ("Delta Freight", "www.deltafreight-logistics.com", 75, "2.5"),
    ("Kaveri Packaging", "www.kaveripackaging-supplies.com", 90, "3"),
)

_CLAUSES = (
    ("Definitions",
     "In this agreement the Buyer means the purchasing company and the Vendor means {v}. "
     "Goods means the items listed in an order form. An order form is a signed request that "
     "names the goods, the quantity, the delivery address and the agreed delivery date. A "
     "business day is any day except a Saturday, a Sunday or a public holiday at the delivery "
     "address."),
    ("Scope of supply",
     "The Vendor supplies the goods in each order form on the terms of this agreement. Each "
     "order form is a separate contract that this agreement governs. If an order form and this "
     "agreement differ, this agreement controls unless the order form says that it overrides a "
     "named clause. The Vendor may not subcontract the whole of an order without written "
     "consent."),
    ("Quality and specification",
     "The goods must match the specification in the order form and the samples that the Vendor "
     "approved. The Vendor keeps a quality record for each batch and gives the Buyer a copy on "
     "request. The Buyer may inspect the goods at the place of manufacture after reasonable "
     "notice. Inspection does not waive any right of the Buyer under this agreement."),
    ("Packaging and marking",
     "The Vendor packs the goods so that they reach the delivery address undamaged by normal "
     "handling. Each package carries the order number, the batch mark, the weight and the "
     "handling instructions. The Vendor bears the cost of packaging unless the order form says "
     "otherwise. Returnable packaging stays the property of the Vendor."),
    ("Delivery",
     "The Vendor delivers the goods to the address in the order form on the agreed date. Risk "
     "passes to the Buyer when the goods are unloaded at that address. Title passes when the "
     "Buyer pays for the goods. The Vendor sends a dispatch note when the goods leave its "
     "premises."),
    ("Acceptance",
     "The Buyer accepts the goods when it has checked them against the order form and found no "
     "defect. The Buyer tells the Vendor of an apparent defect within a reasonable time after "
     "delivery. Acceptance does not limit the warranty in this agreement. A rejected lot "
     "returns to the Vendor at the Vendor's cost."),
    ("Prices",
     "The prices are those in the order form. They include all taxes of the Vendor's country "
     "and exclude the goods and services tax, which the Vendor shows on its invoice. The Vendor "
     "does not raise a price after the Buyer signs an order form. A price list sent later binds "
     "neither party."),
    ("Invoices",
     "The Vendor sends one invoice for each delivery. The invoice shows the order number, the "
     "quantity, the price and the tax. The Vendor sends it to the accounts address in the order "
     "form. An invoice that lacks one of these items is not valid, and the Buyer returns it for "
     "correction."),
    ("Payment", "{pay}"),
    ("Disputed amounts",
     "If the Buyer disputes part of an invoice, it pays the undisputed part and tells the "
     "Vendor the reason for the rest. The parties work to settle a dispute in good faith. "
     "Neither party may stop performing this agreement because of a disputed amount."),
    ("Warranty",
     "The Vendor warrants that the goods are new, free of defects in material and workmanship, "
     "and fit for the purpose in the specification. The warranty runs for twelve months from "
     "acceptance. The Vendor repairs or replaces a defective item at its own cost."),
    ("Intellectual property",
     "Each party keeps the rights that it held before this agreement. The Vendor grants the "
     "Buyer a licence to use the goods and any software in them for the Buyer's business. The "
     "Vendor promises that the goods do not infringe the rights of a third party."),
    ("Confidentiality",
     "Each party keeps the other's non-public information secret and uses it only for this "
     "agreement. This duty does not apply to information that is public through no fault of "
     "the receiving party. The duty continues for three years after this agreement ends."),
    ("Delay in delivery", "{late}"),
    ("Force majeure",
     "Neither party is liable for a delay that an event beyond its control causes, such as a "
     "flood, a fire, a war or an order of a public authority. The party that suffers the event "
     "tells the other at once and works to limit the delay. If the event lasts more than two "
     "months, either party may end the affected order."),
    ("Insurance",
     "The Vendor keeps product liability insurance and cargo insurance that cover the goods "
     "until delivery. The Vendor gives the Buyer a certificate of insurance on request. The "
     "Vendor tells the Buyer if an insurer cancels or changes a policy."),
    ("Compliance",
     "The Vendor obeys the laws that apply to the goods, including the laws on labour, safety, "
     "the environment and bribery. The Vendor keeps the records that prove this and lets the "
     "Buyer audit them after reasonable notice. A breach of this clause is a material breach."),
    ("Liability",
     "Neither party is liable to the other for loss of profit or for indirect loss. The total "
     "liability of the Vendor for all claims in a year does not exceed the price of the goods "
     "that it delivered in that year. This limit does not apply to a breach of confidentiality "
     "or to fraud."),
    ("Termination",
     "Either party may end this agreement by written notice if the other commits a material "
     "breach and does not repair it within a reasonable period after notice. The Buyer may end "
     "this agreement at once if the Vendor becomes insolvent. Clauses that by nature continue "
     "stay in force after the end."),
    ("Notices",
     "A notice under this agreement is in writing and goes to the address that the party gives "
     "in the order form. A notice by hand takes effect on delivery. A notice by post takes "
     "effect on the day after it is posted. A notice by email takes effect when the receiver "
     "reads it."),
    ("Assignment",
     "Neither party may assign its rights under this agreement without the written consent of "
     "the other. Consent is not unreasonably withheld. A change in control of the Vendor needs "
     "the same consent."),
    ("Dispute resolution",
     "The parties first try to settle a dispute by talks between senior managers. If the talks "
     "fail, the dispute goes to a single arbitrator whom the parties choose together. The seat "
     "of the arbitration is the city of the Buyer's registered office. The language is "
     "English."),
    ("Governing law",
     "This agreement is governed by the law of the country of the Buyer's registered office. "
     "The courts of that country hear any matter that arbitration does not decide."),
    ("Entire agreement",
     "This agreement and the order forms are the whole agreement of the parties. They replace "
     "every earlier talk, offer and document on the same subject. A change needs a written "
     "document that both parties sign."),
)

#: A closing sentence group that pads each plain clause, as real contracts do.
_PADDING = (
    " The parties agree that a reference to a clause is a reference to a clause of this "
    "agreement, that a heading is only a guide, and that the singular includes the plural. A "
    "right that this agreement gives is in addition to any right that the law gives. A delay "
    "in the use of a right is not a waiver of it."
)


#: The question of turn 2. It lives here, and not in the scenario file, because
#: ``test_no_second_number_check`` refuses a harness file that holds "lakh" beside
#: a regex. Answer: Kaveri, 3 percent a week, one full week, Rs 15,000.
STRICTEST_QUESTION = (
    "Which vendor has the strictest late-delivery penalty, and how much would a "
    "10-day delay on a 5 lakh order cost with them?"
)

#: Where the two facts sit, in characters from the start. The payment term comes
#: after ``PAY_AFTER`` and the penalty after ``PENALTY_AFTER``, and both move by
#: vendor. A plain ``fetch_page`` (8,000 characters) reads neither one.
PAY_AFTER = 20_000
PENALTY_AFTER = 30_000
SIZE = 40_000


def _blocks(vendor: str) -> Iterator[tuple[str, str]]:
    """The plain clauses, then the same clauses again as schedules, with no end.
    A clause that holds a fact (payment, delay) is never repeated."""
    plain = [(t, b.format(v=vendor)) for t, b in _CLAUSES if "{pay}" not in b and "{late}" not in b]
    cycle = 0
    while True:
        for title, body in plain:
            yield (title if cycle == 0 else f"{title} (Schedule {cycle})"), body + _PADDING
        cycle += 1


def contract_text(vendor: str, pay_days: int, penalty_percent: str) -> str:
    """The supply agreement of *vendor*: about 40,000 characters. The payment term
    follows character 20,000 and the penalty follows character 30,000. Both
    positions move by vendor (its index in :data:`CONTRACTS`)."""
    index = next((k for k, c in enumerate(CONTRACTS) if c[0] == vendor), 0)
    pay = (f"The Buyer pays each valid invoice within {pay_days} days of the date on which it "
           "receives that invoice. Payment is by bank transfer to the account in the order "
           "form. A payment that falls on a day that is not a business day is due on the next "
           "business day.")
    late = ("If the Vendor delivers after the agreed date, the Vendor pays the Buyer a penalty "
            f"of {penalty_percent} percent of the order value for each full week of delay, up "
            "to a limit of ten percent of that value. The Buyer may deduct the penalty from "
            "any invoice. The penalty is the only remedy for a delay of less than six weeks.")
    pending = [(PAY_AFTER + 900 * index, "Payment of invoices", pay),
               (PENALTY_AFTER + 1300 * index, "Delay in delivery", late)]
    lines = [f"SUPPLY AGREEMENT between the Buyer and {vendor}", ""]
    size, number = len(lines[0]), 0
    source = _blocks(vendor)
    while pending or size < SIZE:
        if pending and size >= pending[0][0]:
            _, title, body = pending.pop(0)
        else:
            title, body = next(source)
        number += 1
        chunk = [f"{number}. {title}", body, ""]
        lines.extend(chunk)
        size += sum(len(c) + 1 for c in chunk)
    return "\n".join(lines)
