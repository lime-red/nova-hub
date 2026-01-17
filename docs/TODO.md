Sequence Gap Alerts
* The sequence gap detector is now throwing new alerts for every packet.  We have wrapped around from 999 to 000, so perhaps it is detecting that the last packet was 999 and the next one is 8, and maybe we're missing 1-7?  I don't know.
* There is no way for alerts to be dismissed/resolved.  This should be an admin function.

Capture Running Stats
* Need to start running scores_command, routeinfo_command, bbsinfo_command.  Capture the output (text files) and display these to users.  This should be run after each packet is processed.  The data is going to get quite big after a while, so we need a strategy for managing it.  Perhaps a time-series database, or sqlite.  
* Some last packet data can be correlated to our own data.  We can also identify if packets are taking a long time to arrive (take into account timezones; packet data reflects the timezone of the originating board)
* Adjustable retention precision (everything for a week, after that one datapoint per hour, then one datapoint per day

)
