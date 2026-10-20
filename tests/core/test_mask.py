"""Tests for masking values under sensitive keys. All data here is fake."""

import json
import pickle
import random
import re

import pytest
from ruamel.yaml import YAML

from galley.core.errors import MAX_DEPTH
from galley.core.format import dump_value, format_document, load_document
from galley.core.mask import (
    DEFAULT_MASK_KEYS,
    MASK,
    MaskError,
    MaskRules,
    copy_as_tree,
    detect_kind,
    mask_tree,
    preserve_mask,
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
  "phone": []
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
"""

# Every scalar the default rules hide in the documents above.
HIDDEN_SCALARS = [
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
    assert "newsletter-bot" in masked
    assert "Pat Example" in masked


def test_masked_tree_values() -> None:
    result = mask_tree(json.loads(JSON_DOC))
    value = result.value
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


def test_mask_tree_does_not_change_its_input() -> None:
    original = json.loads(JSON_DOC)
    before = json.loads(JSON_DOC)
    mask_tree(original)
    assert original == before


def test_scalar_root_is_not_masked() -> None:
    result = mask_tree("pat.example@example.com")
    assert result.value == "pat.example@example.com"
    assert result.masked_count == 0


@pytest.mark.parametrize(
    "key",
    [
        "email",
        "EMAIL",
        "Email",
        "e-mail",
        "email_address",
        "Email-Address",
        "email address",
        "emailAddress",
        "phone",
        "Phone_Number",
        "MOBILE",
        "ssn",
        "S_S_N",
        "social_security_number",
        "Social Security Number",
        "address",
        "street-address",
        "StreetAddress",
    ],
)
def test_whole_key_names_match(key: str) -> None:
    assert MaskRules().matches(key)
    assert mask_tree({key: "value-from-a-fixture"}).value == {key: MASK}


@pytest.mark.parametrize(
    "key",
    [
        "emailer",
        "emails",
        "user_email",
        "email1",
        "phones",
        "telephone",
        "home_address",
        "ip_address",
        "addressee",
        "ssn_last4",
        "name",
        "",
    ],
)
def test_partial_key_names_do_not_match(key: str) -> None:
    assert not MaskRules().matches(key)
    assert mask_tree({key: "kept"}).value == {key: "kept"}


@pytest.mark.parametrize("key", [1, None, True, 2.5, (1, 2)])
def test_non_string_keys_never_match(key: object) -> None:
    assert not MaskRules().matches(key)


def test_default_keys_are_the_brief_list() -> None:
    assert DEFAULT_MASK_KEYS == (
        "email",
        "emailaddress",
        "phone",
        "phonenumber",
        "mobile",
        "ssn",
        "socialsecuritynumber",
        "address",
        "streetaddress",
    )


def test_custom_keys_replace_the_defaults() -> None:
    rules = MaskRules(keys=("Api_Key", "token"))
    tree = {"api-key": "abc123", "TOKEN": "t-1", "email": "a@example.com"}
    assert mask_tree(tree, rules).value == {
        "api-key": MASK,
        "TOKEN": MASK,
        "email": "a@example.com",
    }
    assert rules.keys == ("Api_Key", "token")


def test_blank_custom_keys_match_nothing() -> None:
    rules = MaskRules(keys=("", "_-", " "))
    assert mask_tree({"": "x", "-": "y"}, rules).masked_count == 0


def test_mask_rules_pickle() -> None:
    rules = MaskRules(keys=("email", "token"))
    restored = pickle.loads(pickle.dumps(rules))
    assert restored == rules
    assert restored.matches("Token")


def test_mask_rules_accepts_a_list() -> None:
    rules = MaskRules(keys=["email"])  # type: ignore[arg-type]
    assert rules.keys == ("email",)
    assert hash(rules) == hash(MaskRules(keys=("email",)))


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
    assert value["profile"] == {"name": "Pat Example", "email": MASK}
    assert value["owner"] is value["profile"]
    assert value["owner"]["email"] == MASK


@pytest.mark.parametrize("target", ["json", "yaml"])
def test_aliases_keep_the_line_count(target: str) -> None:
    plain = format_document(ALIAS_DOC, "yaml", target)
    masked = dump_value(mask_tree(load_document(ALIAS_DOC, "yaml")).value, target)
    assert _line_count(masked) == _line_count(plain)
    for secret in (
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
    tree = {"email": _nested(MAX_DEPTH - 1, "deep@example.com")}
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


def test_replacement_failure_fails_the_whole_mask() -> None:
    tree = {"name": "Pat Example", "email": object()}
    with pytest.raises(MaskError, match="TypeError"):
        copy_as_tree(tree, "masked", rng=random.Random(1))
    with pytest.raises(MaskError, match="TypeError"):
        copy_as_tree(tree, "fake", rng=random.Random(1))


def test_preserve_mask_keeps_the_email_domain() -> None:
    rng = random.Random(7)
    assert preserve_mask("jdoe@mail.example.com", "email", rng) == (
        "xxxx@mail.example.com"
    )
    assert preserve_mask("first.last@example.com", "email", rng) == (
        "xxxxxxxxxx@example.com"
    )


@pytest.mark.parametrize(
    "phone",
    ["+1 (555) 010-0199", "555-0142", "555.010.0123", "+44 20 7946 0000", "5550100"],
)
def test_preserve_mask_phone_changes_only_the_last_four_digits(phone: str) -> None:
    rng = random.Random(3)
    masked = preserve_mask(phone, "phone", rng)
    assert isinstance(masked, str)
    assert len(masked) == len(phone)
    digit_positions = [index for index, char in enumerate(phone) if char.isdigit()]
    tail = set(digit_positions[-4:])
    for index, (before, after) in enumerate(zip(phone, masked, strict=True)):
        if index in tail:
            assert after.isdigit()
        else:
            assert after == before


def test_preserve_mask_phone_tail_is_random() -> None:
    results = {
        preserve_mask("555-0142", "phone", random.Random(seed)) for seed in range(8)
    }
    assert len(results) > 1


def test_preserve_mask_other_text_keeps_shape() -> None:
    masked = preserve_mask("Unit 7B, Example Street", "other", random.Random(5))
    assert isinstance(masked, str)
    assert len(masked) == len("Unit 7B, Example Street")
    assert re.fullmatch(r"xxxx \dx, xxxxxxx xxxxxx", masked)


def test_preserve_mask_email_without_at_sign_falls_back_to_text() -> None:
    assert preserve_mask("n/a", "email", random.Random(1)) == "x/x"


@pytest.mark.parametrize("number", [7, 4021, -385, 10**20])
def test_preserve_mask_int_keeps_type_sign_and_digit_count(number: int) -> None:
    masked = preserve_mask(number, "other", random.Random(11))
    assert type(masked) is int
    assert (masked < 0) == (number < 0)
    assert len(str(abs(masked))) == len(str(abs(number)))


@pytest.mark.parametrize("number", [12.75, -3.5, 0.125, 1.5e-07, 2.5e20, 100.0])
def test_preserve_mask_float_keeps_type_and_digits(number: float) -> None:
    masked = preserve_mask(number, "other", random.Random(13))
    assert type(masked) is float
    assert (masked < 0) == (number < 0)
    assert len(repr(masked)) == len(repr(number))


@pytest.mark.parametrize("flag", [True, False])
def test_preserve_mask_keeps_bools(flag: bool) -> None:
    assert preserve_mask(flag, "other", random.Random(1)) is flag


def test_preserve_mask_keeps_none() -> None:
    assert preserve_mask(None, "other") is None


def test_preserve_mask_seeded_rng_is_deterministic() -> None:
    first = preserve_mask("Unit 70, +1 555 010 0199", "other", random.Random(42))
    second = preserve_mask("Unit 70, +1 555 010 0199", "other", random.Random(42))
    assert first == second


@pytest.mark.parametrize(
    ("key", "value", "kind"),
    [
        ("email", "n/a", "email"),
        ("work_email", "pat@example.com", "email"),
        ("phone_number", "unknown", "phone"),
        ("Mobile", 5550100, "phone"),
        ("ssn", "000-12-3456", "other"),
        ("address", "pat@example.com", "other"),
        ("contact", "pat@example.com", "email"),
        ("contact", "+1 (555) 010-0199", "phone"),
        ("contact", "555-0142", "phone"),
        (None, "555.010.0123", "phone"),
        ("since", "2026-10-20", "other"),
        ("zip", "00000", "other"),
        ("street", "100 Example Street", "other"),
        ("count", 5550100, "other"),
    ],
)
def test_detect_kind(key: object, value: object, kind: str) -> None:
    assert detect_kind(key, value) == kind


def test_copy_as_original_is_the_value() -> None:
    value = load_document(YAML_DOC, "yaml")
    assert copy_as_tree(value, "original") is value


def test_copy_as_rejects_unknown_modes() -> None:
    with pytest.raises(ValueError, match="Copy As"):
        copy_as_tree({}, "redacted")  # type: ignore[arg-type]


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


@pytest.mark.parametrize("mode", ["masked", "fake"])
def test_copy_as_changes_only_matched_values(mode: str) -> None:
    value = load_document(JSON_DOC, "json")
    copied = copy_as_tree(value, mode, rng=random.Random(21))  # type: ignore[arg-type]
    assert isinstance(copied, dict)
    assert copied["name"] == "Pat Example"
    assert copied["emailer"] == "newsletter-bot"
    assert copied["contacts"][0]["label"] == "work"
    assert copied["email"] != "pat.example@example.com"
    assert copied["email"].endswith("@example.com")
    assert type(copied["address"]["unit"]) is int
    assert type(copied["address"]["geo"][0]) is float


def test_copy_as_masked_keeps_formats() -> None:
    value = load_document(JSON_DOC, "json")
    copied = copy_as_tree(value, "masked", rng=random.Random(4))
    assert isinstance(copied, dict)
    assert copied["email"] == "xxxxxxxxxxx@example.com"
    assert copied["phone_number"].startswith("+1 (555) 010-")
    assert copied["address"]["street"].endswith(" xxxxxxx xxxxxx")
    assert copied["address"]["verified"] is True
    assert copied["address"]["note"] is None


def test_copy_as_fake_gives_new_values_each_time() -> None:
    value = load_document(JSON_DOC, "json")
    first = copy_as_tree(value, "fake")
    second = copy_as_tree(value, "fake")
    assert isinstance(first, dict) and isinstance(second, dict)
    assert first["email"] != second["email"]


def test_copy_as_masks_aliases_too() -> None:
    value = load_document(ALIAS_DOC, "yaml")
    copied = copy_as_tree(value, "masked", rng=random.Random(2))
    assert isinstance(copied, dict)
    assert copied["fallback"] == "xxxxxxxxxxxxx@example.com"
    assert copied["owner"]["email"] == "xxxxxxxxxxx@example.com"


LONG_ADDRESS = (
    "address: 4821 Example Boulevard Apartment 1234 Northfield Township "
    "Example County Testville 00000\nname: Pat Example\n"
)


def test_format_keeping_mask_keeps_wrapped_yaml_lines() -> None:
    plain = format_document(LONG_ADDRESS, "yaml", "yaml")
    value = load_document(LONG_ADDRESS, "yaml")
    for mode in ("masked", "fake"):
        copied = dump_value(copy_as_tree(value, mode, rng=random.Random(8)), "yaml")  # type: ignore[arg-type]
        assert _line_count(copied) == _line_count(plain) == 3


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Open question for #66: the YAML emitter wraps a string longer than "
        "its 80-column width over several lines, and '•••' fits on one. "
        "Reported under the stop rule instead of inventing a rule."
    ),
)
def test_masked_long_yaml_string_keeps_the_line_count() -> None:
    plain = format_document(LONG_ADDRESS, "yaml", "yaml")
    masked = dump_value(mask_tree(load_document(LONG_ADDRESS, "yaml")).value, "yaml")
    assert _line_count(masked) == _line_count(plain)
