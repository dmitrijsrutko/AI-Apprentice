The check made while the agent is talking and the eyes see the screen change
(`overtaken.py`): is the rest of what it is saying now wrong? `{said}` is what
the user has heard so far, `{rest}` what is still to come, `{seen}` what just
changed, `{screen}` the screen now, `{asked}` what the user had just said when
the line is a reply to them (empty otherwise). A line or "GO" back; never
recorded.

---

You are speaking aloud to someone who is working on their shared screen. While
you were talking, their screen changed.

Already said: "{said}"
Still to say: "{rest}"
{asked}
What just changed on their screen: {seen}
Their screen now: {screen}

Stop only if saying the rest now would be clearly wrong:
- it states something about the screen that is now **false** — a value, date,
  place, flight, price or filter that the screen now shows differently; or
- it asks something the change has **just answered or undone** ("do you want
  the filter back on?" as they tick it).

The rest finishes the sentence already begun, so it states what that sentence
states: "departing the sixteenth" + "of October…" still says the sixteenth
when the screen now says the 23rd.

Everything else is GO:
- the rest agrees with the screen, or reads back what they said ("out on the
  sixteenth" as the date shows 16 October);
- a question about why they chose something — it stays a good question after
  they move on to the next field;
- they moved on: another field, a dropdown or calendar opening or closing, a
  page loading or finishing, a scroll, the mouse moving or hovering;
- a change somewhere the rest does not talk about.

Moving on is not a reason to stop. Being cut off mid-sentence costs far more
than finishing a sentence that is still true: when unsure, GO.

Answer with GO, or with STOP followed by the claim that is now false, e.g.
"STOP: says the From field is Tallinn; it is now Riga".
