# The self-cut check's eval

`uv run --env-file .env python scripts/overtaken_eval.py`: anonymised cases from live sessions, each run through the real check on the production engine. A wrong STOP cuts a true line mid-sentence.

After: STOP only on a named false claim (the sentence being finished counts); a reply carries the user's words.

| Case | Want | Got | Right |
|---|---|---|---|
| From changed to Riga, line says Tallinn | STOP | STOP STOP STOP | 3/3 |
| Filter re-ticked, line asks to put it back | STOP | STOP STOP GO | 2/3 |
| Date changed, line states the old date | STOP | STOP STOP STOP | 3/3 |
| Scroll in the airline list | GO | GO GO GO | 3/3 |
| Page finished loading | GO | GO GO GO | 3/3 |
| Skyscanner opened while asking about Skyscanner | GO | GO GO GO | 3/3 |
| Read-back of the dates the user just said, return date set | GO | GO GO GO | 3/3 |
| Why-question about Riga while typing the destination | GO | GO GO GO | 3/3 |
| Hover over a date in the calendar | GO | GO GO GO | 3/3 |
| Dropdown opened after choosing one stop | GO | GO GO GO | 3/3 |

**29/30 right · wrong STOP 0 · wrong GO 1 · median 753 ms · deepseek-flash**
