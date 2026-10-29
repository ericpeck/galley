"""Document size thresholds shared by the window and the editors.

These used to live in ``main_window.py``. The editor gates syntax colors on
``JSON_BUSY_BYTES`` too, and ``main_window`` imports the editor, so the
numbers moved here to keep the imports one-way.

Final size thresholds, in bytes. 1 KB is 1000 bytes and 1 MB is 1_000_000
bytes, the same SI units the size warning shows. YAML busy and progress
limits depend on the document: a tab takes the pure parser, so those
documents reach each state at a smaller size. The warning limit is the same
either way.
"""

_SI_KB = 1000
_SI_MB = 1_000_000
YAML_BUSY_BYTES = 192 * _SI_KB
YAML_TAB_BUSY_BYTES = 96 * _SI_KB
YAML_PROGRESS_BYTES = 704 * _SI_KB
YAML_TAB_PROGRESS_BYTES = 352 * _SI_KB
YAML_WARNING_BYTES = 352 * _SI_KB
JSON_BUSY_BYTES = 5 * _SI_MB
JSON_PROGRESS_BYTES = 20 * _SI_MB
JSON_WARNING_BYTES = 100 * _SI_MB
# XML (#63) uses the YAML numbers until the size bench ``format_xml`` row
# sets its own (E4 on #63). They also gate XML syntax colors.
XML_BUSY_BYTES = YAML_BUSY_BYTES
XML_PROGRESS_BYTES = YAML_PROGRESS_BYTES
XML_WARNING_BYTES = YAML_WARNING_BYTES
# HTML takes the same placeholders. Its Format is pure Python and slower.
HTML_BUSY_BYTES = YAML_BUSY_BYTES
HTML_PROGRESS_BYTES = YAML_PROGRESS_BYTES
HTML_WARNING_BYTES = YAML_WARNING_BYTES
# Format refuses XML and HTML larger than this, with #104's message (Eric,
# #63): one limit and one message for both. JSON and YAML have none.
MARKUP_FORMAT_LIMIT_BYTES = 2 * _SI_MB
# Compare asks first when either side is over this many UTF-8 bytes (Eric,
# #65, 2026-10-25): one guard for JSON and YAML, with no hard refusal.
COMPARE_WARNING_BYTES = 2 * _SI_MB
# Convert from Python runs on the UI thread with no busy indicator. Above
# these UTF-8 sizes of Input, it is refused so the window stays responsive.
# The size bench rows ``python_to_json`` and ``python_to_yaml`` time them.
PYTHON_TO_JSON_BYTES = 1 * _SI_MB
PYTHON_TO_YAML_BYTES = YAML_BUSY_BYTES

# Copy As (#66) builds Masked and Random fake on the worker unless the text
# is smaller than these, so the UI thread stays inside its 100 ms budget.
# Copy As walks the whole tree in Python, and YAML loads with the pure
# parser. On an Apple M4 Pro, Random fake took 11 ms for 32 KB of JSON and
# 14 ms for 2 KB of YAML, which leaves room for a machine about 4x slower.
# Interim values; the size bench ``copy_as`` rows time Copy As.
JSON_COPY_AS_INLINE_BYTES = 32 * _SI_KB
YAML_COPY_AS_INLINE_BYTES = 2 * _SI_KB
