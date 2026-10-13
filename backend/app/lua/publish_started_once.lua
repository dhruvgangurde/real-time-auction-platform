#!lua
--[[
Broadcast auction_started at most once per marker lifetime (brief 12.1, the FR-62 pattern).

The scheduler reaches this from two paths: activation, right after the scheduled -> live commit,
and the repair pass, for a recently started live auction whose marker is missing (a crash or a
Redis error between the commit and the publish). Setting the marker and publishing are one
atomic call, so a repeated activation or a repair after a successful publish sends nothing.

KEYS[1]  started_sent:<auction id>  outside the auction:{<id>}: prefix, so live-state cleanup
                                    never removes it
ARGV[1]  the auction's room channel
ARGV[2]  the auction_started payload (JSON, built from the committed Postgres row)
ARGV[3]  marker lifetime in seconds

Returns 1 if published, 0 if the marker already existed.
]]

if redis.call('SET', KEYS[1], '1', 'NX', 'EX', ARGV[3]) then
  redis.call('PUBLISH', ARGV[1], ARGV[2])
  return 1
end
return 0
