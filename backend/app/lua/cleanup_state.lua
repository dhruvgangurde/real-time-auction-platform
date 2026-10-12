#!lua
--[[
Remove the live state of an auction that Postgres says is neither live nor closing (D2, DC-1).

The caller read Postgres first; this script re-checks inside Redis, atomically, so it never
removes state that may be in use:
  - a hash whose own status is live, closing or rebuilding is kept (the auction may have been
    activated after the caller read Postgres, or a rebuild is between init and finalize);
  - a non-empty outbox is kept (accepted bids or proxy changes not yet persisted).
The dead-letter stream is never touched.

KEYS[1]  auction:{<id>}:state
KEYS[2]  auction:{<id>}:bids      outbox
KEYS[3]  auction:{<id>}:proxies

Returns 1 if the keys were deleted, 0 if they were kept.
]]

local status = redis.call('HGET', KEYS[1], 'status')
if status == 'live' or status == 'closing' or status == 'rebuilding' then
  return 0
end
if redis.call('XLEN', KEYS[2]) > 0 then
  return 0
end
redis.call('DEL', KEYS[1], KEYS[2], KEYS[3])
return 1
