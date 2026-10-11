import { describe, expect, it } from "vitest";
import {
  canConvert,
  convertBody,
  initialChoice,
  isConverted,
  matchContact,
  matchOrganization,
  similarOrganizations,
} from "./convert";
import type { Contact, Lead, Organization } from "./types";

const CONTACTS: Contact[] = [
  { id: "c1", first_name: "Anitha", last_name: "Kumar", email: "Anitha@Bosch.in" },
  { id: "c2", first_name: "Priya", last_name: "Nair", email: "priya@tvs.in" },
];

const ORGS: Organization[] = [
  { id: "o1", name: "Bosch India" },
  { id: "o2", name: "bosch india pvt ltd" },
  { id: "o3", name: "TVS Motor" },
];

function lead(over: Partial<Lead> = {}): Lead {
  return {
    id: "l1",
    lead_name: "Anitha Kumar",
    first_name: "Anitha",
    email: "anitha@bosch.in",
    organization_name: "Bosch India",
    ...over,
  };
}

describe("matchContact", () => {
  it("matches on the address, in any casing", () => {
    // The one dedup rule §3.7 states: an address is the same person however
    // it is spelled, and the Zoho import will bring in both spellings.
    expect(matchContact(lead(), CONTACTS)?.id).toBe("c1");
  });

  it("matches nobody when the lead has no address", () => {
    // A lead with no email is not "the same person as" the first contact
    // with no email — the modal offers Create new instead.
    expect(matchContact(lead({ email: null }), CONTACTS)).toBeNull();
    expect(matchContact(lead({ email: "  " }), CONTACTS)).toBeNull();
  });

  it("matches nobody for an unseen address", () => {
    expect(matchContact(lead({ email: "new@x.in" }), CONTACTS)).toBeNull();
  });
});

describe("matchOrganization", () => {
  it("matches the exact name only", () => {
    // Deliberately not fuzzy: a near-match that silently attaches a deal to
    // the wrong account is worse than a duplicate somebody can merge.
    expect(matchOrganization(lead(), ORGS)?.id).toBe("o1");
    expect(
      matchOrganization(lead({ organization_name: "Bosch" }), ORGS)
    ).toBeNull();
  });

  it("skips the organization entirely for an individual buyer", () => {
    // Minting an organization row named after a person is how a CRM fills
    // with noise.
    expect(matchOrganization(lead({ organization_name: null }), ORGS)).toBeNull();
  });
});

describe("similarOrganizations", () => {
  it("surfaces the near-misses the exact rule will not take", () => {
    // The modal is where a human resolves them — the server never will.
    const similar = similarOrganizations(lead(), ORGS);
    expect(similar.map((o) => o.id)).toContain("o2");
    expect(similar.map((o) => o.id)).not.toContain("o3");
  });

  it("EXCLUDES the exact match, which is rendered above it", () => {
    // The defect: the filter compared a lowercased candidate against a
    // non-lowercased lead name, so the exact match never matched its own
    // exclusion — the modal drew "Bosch India" twice, and because both
    // entries carried the same id, both drew as selected.
    const exact = matchOrganization(lead(), ORGS);
    expect(exact?.id).toBe("o1");
    expect(similarOrganizations(lead(), ORGS).map((o) => o.id)).not.toContain(
      exact!.id
    );
  });

  it("renders no organization twice", () => {
    const shown = [
      ...(matchOrganization(lead(), ORGS) ? ["o1"] : []),
      ...similarOrganizations(lead(), ORGS).map((o) => o.id),
    ];
    expect(new Set(shown).size).toBe(shown.length);
  });

  it("keeps a case-variant visible, because it is NOT the exact match", () => {
    // `matchOrganization` is case-sensitive, so "bosch india pvt ltd" and any
    // other casing are exactly the near-misses this list exists to surface.
    // Folding both sides to dedupe would have hidden them along with the
    // duplicate.
    const orgs = [...ORGS, { id: "o4", name: "BOSCH INDIA" }];
    expect(similarOrganizations(lead(), orgs).map((o) => o.id)).toContain("o4");
  });

  it("is empty when the lead names no company", () => {
    expect(similarOrganizations(lead({ organization_name: "" }), ORGS)).toEqual([]);
  });
});

describe("initialChoice", () => {
  it("opens on whatever the server would have decided", () => {
    // So the modal is a confirmation, not a second guess: the pre-selection
    // IS §3.7's rule, shown.
    const choice = initialChoice(lead(), CONTACTS, ORGS);
    expect(choice.contact).toEqual({ kind: "existing", id: "c1" });
    expect(choice.organization).toEqual({ kind: "existing", id: "o1" });
  });

  it("offers Create new where the server would create", () => {
    const choice = initialChoice(
      lead({ email: "new@x.in", organization_name: "Fresh Ltd" }),
      CONTACTS,
      ORGS
    );
    expect(choice.contact).toEqual({ kind: "new" });
    expect(choice.organization).toEqual({ kind: "new" });
  });

  it("offers no organization at all for an individual buyer", () => {
    const choice = initialChoice(
      lead({ organization_name: "  " }),
      CONTACTS,
      ORGS
    );
    expect(choice.organization).toEqual({ kind: "none" });
  });

  it("names the deal for the company, falling back to the lead", () => {
    expect(initialChoice(lead(), CONTACTS, ORGS).deal.name).toBe("Bosch India");
    expect(
      initialChoice(lead({ organization_name: null }), CONTACTS, ORGS).deal.name
    ).toBe("Anitha Kumar");
  });
});

describe("convertBody", () => {
  it("sends an id only for 'use this one'", () => {
    // §3.7: absent means "work it out". Re-sending the id the modal matched
    // would look identical while meaning something different the day the
    // server's rule changes.
    const body = convertBody({
      contact: { kind: "existing", id: "c1" },
      organization: { kind: "new" },
      deal: { name: "Bosch India", amount: "", expected_close_date: "" },
    });
    expect(body).toEqual({ contact_id: "c1", deal: { name: "Bosch India" } });
  });

  it("omits the organization for an individual buyer", () => {
    const body = convertBody({
      contact: { kind: "new" },
      organization: { kind: "none" },
      deal: { name: "Walk-in", amount: "", expected_close_date: "" },
    });
    expect(body.company_id).toBeUndefined();
  });

  it("parses a stated amount and drops an unparseable one", () => {
    // A typo must not silently become a deal worth NaN rupees.
    const withAmount = convertBody({
      contact: { kind: "new" },
      organization: { kind: "none" },
      deal: { name: "Bosch", amount: "250000", expected_close_date: "" },
    });
    expect(withAmount.deal?.amount).toBe(250000);

    const withTypo = convertBody({
      contact: { kind: "new" },
      organization: { kind: "none" },
      deal: { name: "Bosch", amount: "two lakh", expected_close_date: "" },
    });
    expect(withTypo.deal?.amount).toBeUndefined();
  });

  it("omits the deal block entirely when nothing was stated", () => {
    const body = convertBody({
      contact: { kind: "new" },
      organization: { kind: "none" },
      deal: { name: "  ", amount: "", expected_close_date: "" },
    });
    expect(body.deal).toBeUndefined();
  });
});

describe("guards", () => {
  it("requires a deal name before the modal can submit", () => {
    const base = {
      contact: { kind: "new" as const },
      organization: { kind: "none" as const },
    };
    expect(
      canConvert({
        ...base,
        deal: { name: "Bosch", amount: "", expected_close_date: "" },
      })
    ).toBe(true);
    expect(
      canConvert({
        ...base,
        deal: { name: "   ", amount: "", expected_close_date: "" },
      })
    ).toBe(false);
  });

  it("keys 'converted' on the deal link, never on the timestamp", () => {
    // B6: deleting the deal SET-NULLs the link and the lead returns to the
    // working list. The timestamp survives as history and would strand it.
    expect(isConverted({ converted_deal_id: "d1" })).toBe(true);
    expect(isConverted({ converted_deal_id: null })).toBe(false);
  });
});
