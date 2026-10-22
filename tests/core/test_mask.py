"""Tests for masking sensitive values. All data here is fake."""

import json
import pickle
import random
import re
import typing

import pytest
from ruamel.yaml import YAML

from galley.core import mask
from galley.core.errors import MAX_DEPTH
from galley.core.format import (
    YAML_DUMP_WIDTH,
    _dump_parsed,
    _dump_yaml_value,
    dump_value,
    format_document,
    load_document,
)
from galley.core.mask import (
    DEFAULT_MASK_KEYS,
    DEFAULT_WHOLE_KEYS,
    MASK,
    MASK_TERMS,
    CopyAsMode,
    MaskError,
    MaskRules,
    copy_as_tree,
    detect_kind,
    looks_like_email,
    looks_like_phone,
    mask_tree,
    split_key,
)

JSON_DOC = """{
  "id": 4021,
  "name": "Pat Example",
  "email": "pat.example@example.com",
  "phone_number": "+1 (555) 010-0199",
  "Social-Security-Number": "000-12-3456",
  "address": {
    "street": "100 Example Street",
    "unit": 7,
    "geo": [12.75, -3.5],
    "verified": true,
    "note": null
  },
  "contacts": [
    {"label": "work", "Email Address": "work.desk@example.com"},
    {"label": "cell", "mobile": "555-0142"}
  ],
  "emailer": "newsletter-bot",
  "tags": ["alpha", "beta"],
  "phone": [],
  "rows": [
    ["Ada Example", "ada@example.com", "555-0123"],
    ["A-1042", "2026-10-20", "1.2.3"]
  ],
  "notes": "write to desk@example.com or call 555-0124"
}
"""

YAML_DOC = """\
id: 4021
name: Pat Example
email: pat.example@example.com
phone_number: +1 (555) 010-0199
Social-Security-Number: 000-12-3456
address:
  street: 100 Example Street
  unit: 7
  geo: [12.75, -3.5]
  verified: true
  note: null
contacts:
  - label: work
    Email Address: work.desk@example.com
  - label: cell
    mobile: 555-0142
emailer: newsletter-bot
tags: [alpha, beta]
phone: []
rows:
  - [Ada Example, ada@example.com, 555-0123]
  - [A-1042, '2026-10-20', 1.2.3]
notes: write to desk@example.com or call 555-0124
"""

# Every scalar the default rules hide in the documents above.
HIDDEN_SCALARS = [
    "Pat Example",
    "pat.example@example.com",
    "+1 (555) 010-0199",
    "000-12-3456",
    "100 Example Street",
    7,
    12.75,
    -3.5,
    True,
    None,
    "work.desk@example.com",
    "555-0142",
    "ada@example.com",
    "555-0123",
]

# Strings the default rules keep in the documents above.
KEPT_STRINGS = [
    "newsletter-bot",
    "Ada Example",
    "A-1042",
    "2026-10-20",
    "1.2.3",
    "write to desk@example.com or call 555-0124",
    "work",
    "cell",
    "alpha",
]

DIRECTIONS = [
    (JSON_DOC, "json", "json"),
    (JSON_DOC, "json", "yaml"),
    (YAML_DOC, "yaml", "yaml"),
    (YAML_DOC, "yaml", "json"),
]

ALIAS_DOC = """\
profile: &who
  name: Pat Example
  email: pat.example@example.com
owner: *who
email: &primary primary.inbox@example.com
fallback: *primary
shared: &shared
  city: Exampleville
  zip: '00000'
address: *shared
again: *shared
"""

# The 35 keys from the masking coverage review (#66), all of which now match.
REVIEW_PROBE_KEYS = [
    "email",
    "Email",
    "e-mail",
    "emailAddress",
    "E_MAIL_ADDRESS",
    "phone",
    "phone_number",
    "mobile",
    "address",
    "street_address",
    "ssn",
    "Social Security Number",
    "workEmail",
    "contact_email",
    "user_email",
    "homePhone",
    "billing_address",
    "phone2",
    "emails",
    "ssn_last4",
    "mail",
    "tel",
    "telephone",
    "cell",
    "name",
    "first_name",
    "dob",
    "birthdate",
    "password",
    "api_key",
    "token",
    "credit_card",
    "iban",
    "passport",
    "ip_address",
]

FAKE_SECRET = "fake-not-a-real-token"


def _line_count(text: str) -> int:
    return len(text.splitlines())


def _load_text(text: str, target: str) -> object:
    if target == "json":
        return json.loads(text)
    return YAML(typ="safe").load(text)


def _shape(value: object) -> object:
    """Keys and nesting with every scalar blanked out."""
    if isinstance(value, dict):
        return {key: _shape(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_shape(child) for child in value]
    return None


def _words(term: str) -> list[str]:
    return term.split()


def _snake(term: str) -> str:
    return "_".join(_words(term))


def _camel(term: str) -> str:
    first, *rest = _words(term)
    return first + "".join(word.capitalize() for word in rest)


def _pascal(term: str) -> str:
    return "".join(word.capitalize() for word in _words(term))


def _term_id(row: tuple[str, str, str]) -> str:
    return f"{row[0]}:{row[1]}"


WORD_TERMS = [row for row in MASK_TERMS if row[2] == "word"]
WHOLE_KEY_TERMS = [row for row in MASK_TERMS if row[2] == "key"]


# Formatting, line count, shape and leaks


@pytest.mark.parametrize(("text", "source", "target"), DIRECTIONS)
def test_dump_value_matches_format_document(text: str, source: str, target: str):
    loaded = load_document(text, source)
    assert dump_value(loaded, target) == format_document(text, source, target)


@pytest.mark.parametrize(("text", "source", "target"), DIRECTIONS)
def test_masked_output_keeps_the_line_count(text: str, source: str, target: str):
    plain = format_document(text, source, target)
    result = mask_tree(load_document(text, source))
    masked = dump_value(result.value, target)
    assert _line_count(masked) == _line_count(plain)
    assert result.masked_count == len(HIDDEN_SCALARS)


@pytest.mark.parametrize(("text", "source", "target"), DIRECTIONS)
def test_masked_output_keeps_keys_and_structure(text: str, source: str, target: str):
    plain = _load_text(format_document(text, source, target), target)
    masked = _load_text(
        dump_value(mask_tree(load_document(text, source)).value, target), target
    )
    assert _shape(masked) == _shape(plain)


@pytest.mark.parametrize(("text", "source", "target"), DIRECTIONS)
def test_no_hidden_value_appears_in_masked_output(text: str, source: str, target: str):
    masked = dump_value(mask_tree(load_document(text, source)).value, target)
    for value in HIDDEN_SCALARS:
        if isinstance(value, str):
            assert value not in masked
    assert "12.75" not in masked
    assert "-3.5" not in masked
    assert "true" not in masked
    assert "null" not in masked
    assert re.search(r"\b7\b", masked) is None
    for value in KEPT_STRINGS:
        assert value in masked


def test_masked_tree_values() -> None:
    result = mask_tree(json.loads(JSON_DOC))
    value = result.value
    assert value["name"] == MASK
    assert value["email"] == MASK
    assert value["phone_number"] == MASK
    assert value["Social-Security-Number"] == MASK
    assert value["address"] == {
        "street": MASK,
        "unit": MASK,
        "geo": [MASK, MASK],
        "verified": MASK,
        "note": MASK,
    }
    assert value["contacts"][0] == {"label": "work", "Email Address": MASK}
    assert value["contacts"][1] == {"label": "cell", "mobile": MASK}
    assert value["emailer"] == "newsletter-bot"
    assert value["phone"] == []
    assert value["id"] == 4021
    assert value["rows"] == [
        ["Ada Example", MASK, MASK],
        ["A-1042", "2026-10-20", "1.2.3"],
    ]
    assert value["notes"] == "write to desk@example.com or call 555-0124"


def test_mask_tree_does_not_change_its_input() -> None:
    original = json.loads(JSON_DOC)
    before = json.loads(JSON_DOC)
    mask_tree(original)
    assert original == before


def _every_term_doc() -> dict[str, str]:
    """One key per table entry, and one per review probe key."""
    doc: dict[str, str] = {}
    for index, (_category, term, _scope) in enumerate(MASK_TERMS):
        doc[_snake(term)] = f"hidden-term-{index:03d}"
    for index, key in enumerate(REVIEW_PROBE_KEYS):
        doc.setdefault(key, f"hidden-probe-{index:02d}")
    return doc


@pytest.mark.parametrize("source", ["json", "yaml"])
@pytest.mark.parametrize("target", ["json", "yaml"])
def test_every_table_entry_masks_without_a_leak(source: str, target: str) -> None:
    doc = _every_term_doc()
    text = dump_value(doc, source)
    plain = format_document(text, source, target)
    result = mask_tree(load_document(text, source))
    masked = dump_value(result.value, target)
    assert result.masked_count == len(doc)
    assert _line_count(masked) == _line_count(plain)
    assert "hidden-" not in masked
    assert _shape(_load_text(masked, target)) == _shape(doc)


# Splitting key names into words


@pytest.mark.parametrize(
    ("key", "words"),
    [
        ("email", ("email",)),
        ("workEmail", ("work", "email")),
        ("WorkEmail", ("work", "email")),
        ("contact_email", ("contact", "email")),
        ("contact-email", ("contact", "email")),
        ("contact email", ("contact", "email")),
        ("contact.email", ("contact", "email")),
        ("E_MAIL_ADDRESS", ("e", "mail", "address")),
        ("eMail", ("e", "mail")),
        ("phone2", ("phone", "2")),
        ("ssn_last4", ("ssn", "last", "4")),
        ("address2Line", ("address", "2", "line")),
        ("IPAddress", ("ip", "address")),
        ("clientIPAddress", ("client", "ip", "address")),
        ("APIKey", ("api", "key")),
        ("userSSN", ("user", "ssn")),
        ("userSSNs", ("user", "ssns")),
        ("SSNsList", ("ssns", "list")),
        ("SSNLast4", ("ssn", "last", "4")),
        ("IPv6", ("i", "pv", "6")),
        ("ipv6", ("ipv", "6")),
        ("HTTPServer", ("http", "server")),
        ("emailer", ("emailer",)),
        ("filename", ("filename",)),
        ("fileName", ("file", "name")),
        ("__name__", ("name",)),
        ("名前Email", ("名前", "email")),
        ("", ()),
        ("_-. ", ()),
        ("1234", ("1234",)),
    ],
)
def test_split_key(key: str, words: tuple[str, ...]) -> None:
    assert split_key(key) == words


# The default table


def test_default_keys_come_from_the_table() -> None:
    assert DEFAULT_MASK_KEYS == tuple(term for _, term, _ in WORD_TERMS)
    assert DEFAULT_WHOLE_KEYS == ("name",)
    assert MaskRules() == MaskRules(DEFAULT_MASK_KEYS, DEFAULT_WHOLE_KEYS)


def test_table_categories_and_scopes() -> None:
    categories = [category for category, _, _ in MASK_TERMS]
    assert set(categories) == {"contact", "identity", "financial", "network", "secret"}
    # Grouped: each category is one run of rows.
    runs = [cat for index, cat in enumerate(categories) if cat != categories[index - 1]]
    assert len(runs) == len(set(categories))
    assert {scope for _, _, scope in MASK_TERMS} == {"word", "key"}


def test_table_terms_are_distinct() -> None:
    joined = ["".join(split_key(term)) for _, term, _ in MASK_TERMS]
    assert len(joined) == len(set(joined))
    assert all(term == term.lower().strip() for _, term, _ in MASK_TERMS)


@pytest.mark.parametrize("row", WORD_TERMS, ids=_term_id)
def test_every_word_term_matches_its_spellings(row: tuple[str, str, str]) -> None:
    term = row[1]
    rules = MaskRules()
    spellings = {
        term,
        _snake(term),
        _snake(term).upper(),
        "-".join(_words(term)),
        _camel(term),
        _pascal(term),
        "".join(_words(term)),
        f"work_{_snake(term)}",
        f"work{_pascal(term)}",
        f"{_camel(term)}_2",
        f"{_snake(term)}_id",
        f"{_camel(term)}s",
    }
    for key in spellings:
        assert rules.matches(key), key
        assert mask_tree({key: FAKE_SECRET}).value == {key: MASK}, key


@pytest.mark.parametrize("row", WHOLE_KEY_TERMS, ids=_term_id)
def test_every_whole_key_term_matches_only_the_whole_key(
    row: tuple[str, str, str],
) -> None:
    term = row[1]
    rules = MaskRules()
    for key in (term, term.upper(), term.capitalize(), f"{term}s", f"_{term}_"):
        assert rules.matches(key), key
        assert mask_tree({key: "Pat Example"}).value == {key: MASK}, key
    for key in (f"file_{term}", f"display{term.capitalize()}", f"{term}_space"):
        assert not rules.matches(key), key
        assert mask_tree({key: "Pat Example"}).value == {key: "Pat Example"}, key


@pytest.mark.parametrize("key", REVIEW_PROBE_KEYS)
def test_review_probe_keys_are_masked(key: str) -> None:
    assert MaskRules().matches(key)
    assert mask_tree({key: FAKE_SECRET}).value == {key: MASK}


def test_review_probe_count() -> None:
    assert len(REVIEW_PROBE_KEYS) == len(set(REVIEW_PROBE_KEYS)) == 35
    assert sum(map(MaskRules().matches, REVIEW_PROBE_KEYS)) == 35


@pytest.mark.parametrize(
    "key",
    [
        "Email",
        "e-mail",
        "E-Mail-Address",
        "email address",
        "emailaddress",
        "phonenumber",
        "PhoneNumbers",
        "streetaddress",
        "socialsecuritynumber",
        "S_S_N",
        "addresses",
        "IPAddress",
        "ipAddress",
        "clientIp",
        "userIP",
        "userSSN",
        "userSSNs",
        "SSNLast4",
        "APIKey",
        "apiKeys",
        "X-Api-Key",
        "MACAddress",
        "accessToken",
        "client_secret",
        "Authorization",
        "dateOfBirth",
        "DOB",
        "CVV2",
        "zipCode",
        "driverLicenseNumber",
        "national_id_number",
        "names",
        "NAME",
    ],
)
def test_more_matching_keys(key: str) -> None:
    assert MaskRules().matches(key)


@pytest.mark.parametrize(
    "key",
    [
        "emailer",
        "mailer",
        "mailbox",
        "filename",
        "fileName",
        "displayName",
        "hostname",
        "hostName",
        "typeName",
        "nameserver",
        "name_2",
        "key",
        "keyId",
        "primaryKey",
        "publicKey",
        "license",
        "author",
        "authority",
        "oauth",
        "secretary",
        "tokenizer",
        "passwordless",
        "addressee",
        "phoneme",
        "telemetry",
        "hotel",
        "cellar",
        "ipsum",
        "skip",
        "macOS",
        "card",
        "account",
        "accountId",
        "id",
        "description",
        "",
        "_-",
        "4021",
    ],
)
def test_keys_that_do_not_match(key: str) -> None:
    assert not MaskRules().matches(key)
    assert mask_tree({key: "kept"}).value == {key: "kept"}


@pytest.mark.parametrize("key", [1, None, True, 2.5, (1, 2)])
def test_non_string_keys_never_match(key: object) -> None:
    assert not MaskRules().matches(key)


def test_custom_keys_replace_the_defaults() -> None:
    rules = MaskRules(keys=("Api_Key", "token"), whole_keys=())
    tree = {"api-key": "abc123", "TOKEN": "t-1", "email": "kept", "name": "Pat"}
    assert mask_tree(tree, rules).value == {
        "api-key": MASK,
        "TOKEN": MASK,
        "email": "kept",
        "name": "Pat",
    }
    assert rules.keys == ("Api_Key", "token")


def test_custom_whole_keys() -> None:
    rules = MaskRules(keys=(), whole_keys=("Customer ID",))
    assert rules.matches("customer_id")
    assert rules.matches("CustomerIDs")
    assert not rules.matches("customer_id_type")


def test_blank_custom_keys_match_nothing() -> None:
    rules = MaskRules(keys=("", "_-", " "), whole_keys=("",))
    assert mask_tree({"": "x", "-": "y"}, rules).masked_count == 0


def test_mask_rules_pickle() -> None:
    rules = MaskRules(keys=("email", "token"), whole_keys=("name",))
    restored = pickle.loads(pickle.dumps(rules))
    assert restored == rules
    assert restored.matches("accessToken")
    assert restored.matches("Name")
    assert not restored.matches("fileName")


def test_mask_rules_accepts_lists() -> None:
    rules = MaskRules(keys=["email"], whole_keys=["name"])  # type: ignore[arg-type]
    assert rules.keys == ("email",)
    assert rules.whole_keys == ("name",)
    assert hash(rules) == hash(MaskRules(keys=("email",), whole_keys=("name",)))


# Values that are an email or a phone number, under any key


@pytest.mark.parametrize(
    "value",
    [
        "pat@example.com",
        "pat.example+news@mail.example.org",
        "PAT_EXAMPLE@EXAMPLE.COM",
        "o'brien@example.co.uk",
        "  pat@example.com  ",
    ],
)
def test_email_values(value: str) -> None:
    assert looks_like_email(value)


@pytest.mark.parametrize(
    "value",
    [
        "pat@example",
        "user@localhost",
        "lodash@4.17.21",
        "@example.com",
        "pat@",
        "pat @example.com",
        "pat@@example.com",
        "Pat <pat@example.com>",
        "mailto:pat@example.com",
        "git@example.com:org/repo.git",
        "email me at pat@example.com",
        "pat@example.c",
        "",
    ],
)
def test_not_email_values(value: str) -> None:
    assert not looks_like_email(value)


@pytest.mark.parametrize(
    "value",
    [
        "555-0142",
        "555 0142",
        "555-555-0123",
        "555.555.0123",
        "555 555 0123",
        "(555) 555-0123",
        "(555)555-0123",
        "+1 (555) 555-0123",
        "+1 555 555 0123",
        "+1-555-555-0123",
        "+15555550123",
        "1-555-555-0123",
        "+44 20 7946 0000",
        "020 7946 0000",
        "01 99 00 12 34",
        "0491 570 006",
        " 555-0142 ",
    ],
)
def test_phone_values(value: str) -> None:
    assert looks_like_phone(value)


@pytest.mark.parametrize(
    "value",
    [
        "12345",
        "5550142",
        "15555550123",
        "4021",
        "2026-10-20",
        "20-10-2026",
        "20.10.2026",
        "2026.10.20",
        "2026 10 20",
        "10/20/2026",
        "2026-10-20T12:30:00Z",
        "2026-10-20 12:30:00",
        "12:30:00",
        "1760918400",
        "1.2.3",
        "v1.2.3",
        "10.20.30.40",
        "192.168.10.20",
        "$1,234.56",
        "1,234,567",
        "1 234 567",
        "12 345 678",
        "12.345.678",
        "1234567.89",
        "123.4567",
        "-5550142",
        "1990-2020",
        "8000-9000",
        "12345-6789",
        "123e4567-e89b-12d3-a456-426614174000",
        "A-1042",
        "ORD-2026-0042",
        "978-0-306-40615-7",
        "0-306-40615-2",
        "555-0142 ext 7",
        "555-0142x7",
        "+1 555 555 0123 4567 8901",
        "(555) 555-0123 or 555-0124",
        "call 555-0142",
        "+",
        "",
    ],
)
def test_not_phone_values(value: str) -> None:
    assert not looks_like_phone(value)


def test_keyless_rows_are_masked() -> None:
    result = mask_tree([["Ada Example", "ada@example.com", "+1 555 555 0100"]])
    assert result.value == [["Ada Example", MASK, MASK]]
    assert result.masked_count == 2


def test_values_are_masked_under_any_key() -> None:
    tree = {
        "contact": "pat@example.com",
        "backup": {"how": "555-0142", "when": "2026-10-20"},
        "list": ["kept", {"x": "(555) 555-0199"}],
    }
    result = mask_tree(tree)
    assert result.value == {
        "contact": MASK,
        "backup": {"how": MASK, "when": "2026-10-20"},
        "list": ["kept", {"x": MASK}],
    }
    assert result.masked_count == 3


def test_a_value_under_a_masked_key_counts_once() -> None:
    result = mask_tree({"email": ["pat@example.com", "n/a"]})
    assert result.value == {"email": [MASK, MASK]}
    assert result.masked_count == 2


def test_free_text_is_not_masked() -> None:
    tree = {
        "notes": "email me at pat@example.com",
        "bio": "call 555-0142 after five",
        "from": "Pat Example <pat@example.com>",
        "lines": ["reach pat@example.com", "or (555) 555-0123, any time"],
    }
    result = mask_tree(tree)
    assert result.value == tree
    assert result.masked_count == 0


def test_numbers_that_look_like_phones_are_not_masked() -> None:
    tree = {"count": 5555550123, "rows": [[5550142, 2026.1020]]}
    assert mask_tree(tree).value == tree


def test_scalar_root_is_masked_by_value() -> None:
    result = mask_tree("pat.example@example.com")
    assert result.value == MASK
    assert result.masked_count == 1
    kept = mask_tree("Pat Example")
    assert kept.value == "Pat Example"
    assert kept.masked_count == 0


@pytest.mark.parametrize("target", ["json", "yaml"])
def test_keyless_values_keep_the_line_count(target: str) -> None:
    text = (
        "- [Ada Example, ada@example.com, 555-0123]\n"
        "- - Bo Example\n  - bo@example.com\n  - '+1 555 555 0124'\n"
        "- just text\n"
    )
    plain = format_document(text, "yaml", target)
    result = mask_tree(load_document(text, "yaml"))
    masked = dump_value(result.value, target)
    assert result.masked_count == 4
    assert _line_count(masked) == _line_count(plain)
    for secret in ("ada@example.com", "555-0123", "bo@example.com", "555 0124"):
        assert secret not in masked
    assert "Bo Example" in masked


# Aliases, depth and failures


def test_alias_under_a_masked_key_is_masked() -> None:
    result = mask_tree(load_document(ALIAS_DOC, "yaml"))
    value = result.value
    assert value["address"] == {"city": MASK, "zip": MASK}
    assert value["again"] is value["address"]
    assert value["shared"] is value["address"]


def test_masked_anchor_does_not_leak_through_an_alias() -> None:
    value = mask_tree(load_document(ALIAS_DOC, "yaml")).value
    assert value["email"] == MASK
    assert value["fallback"] == MASK
    assert value["profile"] == {"name": MASK, "email": MASK}
    assert value["owner"] is value["profile"]
    assert value["owner"]["email"] == MASK


def test_shared_string_under_a_masked_key_is_masked_everywhere() -> None:
    text = "ssn: &id example-ssn-value\nlabel: *id\nother: example-ssn-value\n"
    value = mask_tree(load_document(text, "yaml")).value
    assert value == {"ssn": MASK, "label": MASK, "other": "example-ssn-value"}


@pytest.mark.parametrize("target", ["json", "yaml"])
def test_aliases_keep_the_line_count(target: str) -> None:
    plain = format_document(ALIAS_DOC, "yaml", target)
    masked = dump_value(mask_tree(load_document(ALIAS_DOC, "yaml")).value, target)
    assert _line_count(masked) == _line_count(plain)
    for secret in (
        "Pat Example",
        "pat.example@example.com",
        "primary.inbox@example.com",
        "Exampleville",
        "00000",
    ):
        assert secret not in masked


def test_yaml_anchors_survive_masking() -> None:
    masked = dump_value(mask_tree(load_document(ALIAS_DOC, "yaml")).value, "yaml")
    plain = format_document(ALIAS_DOC, "yaml", "yaml")
    assert masked.count("&id") == plain.count("&id") == 2
    assert masked.count("*id") == plain.count("*id") == 3


def test_shared_container_is_counted_once() -> None:
    shared = {"city": "Exampleville", "zip": "00000"}
    result = mask_tree({"address": shared, "again": shared, "copy": shared})
    assert result.masked_count == 2


def test_recursive_alias_finishes() -> None:
    text = "address: &loop\n  - city: Exampleville\n  - *loop\n"
    value = load_document(text, "yaml")
    result = mask_tree(value)
    masked = result.value
    assert masked["address"][0] == {"city": MASK}
    assert masked["address"][1] is masked["address"]
    assert result.masked_count == 1
    assert _line_count(dump_value(masked, "yaml")) == _line_count(
        format_document(text, "yaml", "yaml")
    )


def _nested(depth: int, leaf: object) -> object:
    value = leaf
    for _ in range(depth):
        value = [value]
    return value


def test_masking_stays_within_max_depth() -> None:
    tree = {"email": _nested(MAX_DEPTH - 1, "kept-deep-value")}
    masked = mask_tree(tree).value
    node = masked["email"]
    for _ in range(MAX_DEPTH - 1):
        node = node[0]
    assert node == MASK


def test_masking_past_max_depth_fails() -> None:
    with pytest.raises(MaskError, match="nesting"):
        mask_tree({"email": _nested(MAX_DEPTH, "deep@example.com")})


def test_yaml_pairs_fail_the_mask() -> None:
    value = load_document("list: !!pairs [email: a@example.com]\n", "yaml")
    with pytest.raises(MaskError, match="pairs"):
        mask_tree(value)


def test_yaml_set_under_a_masked_key_fails_the_mask() -> None:
    value = load_document("email: !!set {a@example.com}\n", "yaml")
    with pytest.raises(MaskError, match="set"):
        mask_tree(value)


def test_yaml_set_outside_masked_keys_is_kept() -> None:
    value = load_document("tags: !!set {alpha, beta}\nemail: a@example.com\n", "yaml")
    masked = mask_tree(value).value
    assert masked == {"tags": {"alpha", "beta"}, "email": MASK}


def test_replacement_failure_fails_the_whole_copy() -> None:
    tree = {"name": "Pat Example", "email": object()}
    with pytest.raises(MaskError, match="TypeError"):
        copy_as_tree(tree, "fake", rng=random.Random(1))


def test_matching_failure_fails_the_whole_mask(monkeypatch) -> None:
    def broken(_name: str) -> tuple[str, ...]:
        raise RuntimeError("boom")

    rules = MaskRules()
    monkeypatch.setattr(mask, "split_key", broken)
    with pytest.raises(MaskError, match="RuntimeError"):
        mask_tree({"email": "pat@example.com"}, rules)


# Kinds for Random fake


@pytest.mark.parametrize(
    ("key", "value", "kind"),
    [
        ("email", "n/a", "email"),
        ("work_email", "pat@example.com", "email"),
        ("e-mail", "n/a", "email"),
        ("emails", ["n/a"], "email"),
        ("phone_number", "unknown", "phone"),
        ("Mobile", 5550100, "phone"),
        ("homeTel", "n/a", "phone"),
        ("fax", "n/a", "phone"),
        ("phone", "pat@example.com", "email"),
        ("ssn", "000-12-3456", "phone"),
        ("ssn", "n/a", "other"),
        ("address", "pat@example.com", "email"),
        ("contact", "pat@example.com", "email"),
        ("contact", "+1 (555) 010-0199", "phone"),
        ("contact", "555-0142", "phone"),
        (None, "555.555.0123", "phone"),
        (None, "ada@example.com", "email"),
        ("emailer", "n/a", "other"),
        ("since", "2026-10-20", "other"),
        ("zip", "00000", "other"),
        ("street", "100 Example Street", "other"),
        ("count", 5550100, "other"),
    ],
)
def test_detect_kind(key: object, value: object, kind: str) -> None:
    assert detect_kind(key, value) == kind


# Copy As


def test_copy_as_modes() -> None:
    assert typing.get_args(CopyAsMode) == ("original", "masked", "fake")
    assert not hasattr(mask, "preserve_mask")


def test_copy_as_original_is_the_value() -> None:
    value = load_document(YAML_DOC, "yaml")
    assert copy_as_tree(value, "original") is value


def test_copy_as_rejects_unknown_modes() -> None:
    with pytest.raises(ValueError, match="Copy As"):
        copy_as_tree({}, "redacted")  # type: ignore[arg-type]


@pytest.mark.parametrize(("text", "source", "target"), DIRECTIONS)
def test_copy_as_masked_is_the_masked_view(text: str, source: str, target: str):
    value = load_document(text, source)
    copied = dump_value(copy_as_tree(value, "masked"), target)
    assert copied == dump_value(mask_tree(value).value, target)
    assert MASK in copied


@pytest.mark.parametrize("mode", ["original", "masked", "fake"])
@pytest.mark.parametrize(("text", "source", "target"), DIRECTIONS)
def test_copy_as_serializes_to_valid_output(
    mode: str, text: str, source: str, target: str
) -> None:
    value = load_document(text, source)
    copied = dump_value(copy_as_tree(value, mode, rng=random.Random(9)), target)  # type: ignore[arg-type]
    plain = format_document(text, source, target)
    reloaded = _load_text(copied, target)
    assert _shape(reloaded) == _shape(_load_text(plain, target))
    assert _line_count(copied) == _line_count(plain)
    if mode == "original":
        assert copied == plain


@pytest.mark.parametrize(("text", "source", "target"), DIRECTIONS)
def test_copy_as_fake_leaks_no_hidden_value(text: str, source: str, target: str):
    value = load_document(text, source)
    copied = dump_value(copy_as_tree(value, "fake", rng=random.Random(5)), target)
    for hidden in HIDDEN_SCALARS:
        if isinstance(hidden, str):
            assert hidden not in copied
    for kept in KEPT_STRINGS:
        assert kept in copied


def test_copy_as_fake_changes_only_hidden_values() -> None:
    value = load_document(JSON_DOC, "json")
    copied = copy_as_tree(value, "fake", rng=random.Random(21))
    assert isinstance(copied, dict)
    assert copied["name"] != "Pat Example"
    assert len(copied["name"]) == len("Pat Example")
    assert copied["emailer"] == "newsletter-bot"
    assert copied["contacts"][0]["label"] == "work"
    assert copied["email"] != "pat.example@example.com"
    assert copied["email"].endswith("@example.com")
    assert type(copied["address"]["unit"]) is int
    assert type(copied["address"]["geo"][0]) is float
    assert copied["notes"] == "write to desk@example.com or call 555-0124"
    assert copied["rows"][1] == ["A-1042", "2026-10-20", "1.2.3"]


def test_copy_as_fake_replaces_values_found_by_value() -> None:
    value = [["Ada Example", "ada@example.com", "+1 (555) 555-0123"]]
    copied = copy_as_tree(value, "fake", rng=random.Random(3))
    assert isinstance(copied, list)
    name, email, phone = copied[0]
    assert name == "Ada Example"
    assert email != "ada@example.com"
    assert email.endswith("@example.com")
    assert phone != "+1 (555) 555-0123"
    assert re.fullmatch(r"\+\d \(\d{3}\) \d{3}-\d{4}", phone)


def test_copy_as_fake_gives_new_values_each_time() -> None:
    value = load_document(JSON_DOC, "json")
    first = copy_as_tree(value, "fake")
    second = copy_as_tree(value, "fake")
    assert isinstance(first, dict) and isinstance(second, dict)
    assert first["email"] != second["email"]


def test_copy_as_masks_aliases_too() -> None:
    value = load_document(ALIAS_DOC, "yaml")
    for mode in ("masked", "fake"):
        copied = dump_value(copy_as_tree(value, mode, rng=random.Random(2)), "yaml")  # type: ignore[arg-type]
        assert "primary.inbox@example.com" not in copied
        assert "pat.example@example.com" not in copied
        assert "Pat Example" not in copied


# Long YAML lines


LONG_ADDRESS = (
    "address: 4821 Example Boulevard Apartment 1234 Northfield Township "
    "Example County Testville 00000\nname: Pat Example\n"
)


def test_copy_as_keeps_long_yaml_lines() -> None:
    plain = format_document(LONG_ADDRESS, "yaml", "yaml")
    value = load_document(LONG_ADDRESS, "yaml")
    for mode in ("masked", "fake"):
        copied = dump_value(copy_as_tree(value, mode, rng=random.Random(8)), "yaml")  # type: ignore[arg-type]
        assert _line_count(copied) == _line_count(plain) == 2


def test_masked_long_yaml_string_keeps_the_line_count() -> None:
    plain = format_document(LONG_ADDRESS, "yaml", "yaml")
    masked = dump_value(mask_tree(load_document(LONG_ADDRESS, "yaml")).value, "yaml")
    assert _line_count(masked) == _line_count(plain) == 2


@pytest.mark.parametrize("source", ["json", "yaml"])
def test_format_does_not_wrap_a_long_yaml_string(source: str) -> None:
    long_value = " ".join(["Example"] * 400)
    text = (
        json.dumps({"note": long_value, "quoted": "a: " + long_value})
        if source == "json"
        else f"note: {long_value}\nquoted: 'a: {long_value}'\n"
    )
    plain = format_document(text, source, "yaml")
    assert plain == f"note: {long_value}\nquoted: 'a: {long_value}'\n"
    assert plain.splitlines()[0] == f"note: {long_value}"


def test_c_and_pure_yaml_dumps_share_the_width() -> None:
    value = {"note": "Example " * 2000, "items": ["Example Street " * 500]}
    pure = _dump_parsed(value, pure=True)
    assert pure == _dump_parsed(value, pure=False) == _dump_yaml_value(value)
    assert pure == dump_value(value, "yaml")
    assert _line_count(pure) == 3
    assert YAML_DUMP_WIDTH == 2**31 - 1
