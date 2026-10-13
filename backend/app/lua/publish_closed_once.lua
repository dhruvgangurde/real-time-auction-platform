#!lua
--[[
Broadcast auction_closed at most once per marker lifetime (FR-62).

The closer reaches this from two paths: the caller whose closing -> terminal flip returned OK,
and, when there is no live state, the caller that settled. No live state can also mean that
another caller already flipped, broadcast and removed it, so both paths go through this one
marker. Setting the marker and publishing are one atomic call: whichever path comes second
sends nothing.

KEYS[1]  closed_sent:<auction id>   outside the auction:{<id>}: prefix, so live-state cleanup
                                    never removes it
ARGV[1]  the auction's room channel
ARGV[2]  the auction_closed payload (JSON, built from the committed Postgres rows)
ARGV[3]  marker lifetime in seconds

Returns 1 if published, 0 if the marker already existed.
]]

if redis.call('SET', KEYS[1], '1', 'NX', 'EX', ARGV[3]) then
  redis.call('PUBLISH', ARGV[1], ARGV[2])
  return 1
end
return 0
