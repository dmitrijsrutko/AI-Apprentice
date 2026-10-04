# The screen changes while the agent is talking, but nothing it is still to
# say is touched by it (a scroll): it keeps talking to the end.
greeting Hi.
reply Still to say = GO
reply Scrolled down = NOTHING
reply Which flights = You are flying from Tallinn, so the morning airBaltic at six ten is the obvious pick, and it lands in Berlin before eleven.
0.2 listen
1.0 voice on
1.4 partial which flights
2.0 partial which flights work
2.4 voice off
3.4 final Which flights work?
6.0 seen Scrolled down the results list
16 end
