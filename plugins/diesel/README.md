# Diesel Engine (NUT) Plugin

Support for Diesel engine NUT string blocks.

## Features

- Detects string blocks with prefix 0x10 00 00 08
- Reads format: [u32 size][string bytes]
- Encoding: CP932 (Shift-JIS)
- Updates header offsets at 0x08 and 0x0C

## Notes

- Offsets are recalculated during build()
- Replacement is done in reverse order to prevent index shifting
- Assumes only two header offsets require updating
