# The screen changes while the agent is still talking about it: the rest of
# what it was saying is now wrong (Tallinn, when the From field says Riga).
# It stops itself at the word the user last heard and says something fresh.
greeting Hi.
reply Still to say = STOP
reply Tallinn to Riga = Riga now. Why fly from Riga?
reply Which flights = You are flying from Tallinn, so the morning airBaltic at six ten is the obvious pick, and it lands in Berlin before eleven.
0.2 listen
1.0 voice on
1.4 partial which flights
2.0 partial which flights work
2.4 voice off
3.4 final Which flights work?
6.0 seen From field changed from Tallinn to Riga
16 end
