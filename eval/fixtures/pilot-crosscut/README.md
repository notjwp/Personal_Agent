# clean

Normalising user-supplied values. Every field type has its own rule.

## Layout

- `clean/core.py` - the shared normaliser
- `clean/contacts.py` - email and phone
- `clean/address.py` - postcode and full name
- `clean/web.py` - url and public id

## Tests

    pytest -q
