#!lua
--[[
Initialise an auction's live state from a Postgres snapshot (FR-43).

Never overwrites live state. The hash is written with status 'rebuilding', so place_bid and
set_proxy treat it as absent until finalize_state.lua confirms the snapshot is still current
and sets the real status. A rebuild that dies midway is taken over once its lease expires.

KEYS[1]  auction:{<id>}:state
KEYS[2]  auction:{<id>}:bids      outbox; must be empty or the snapshot would miss accepted
                                  bids and proxy changes
KEYS[3]  auction:{<id>}:proxies   armed maxima, bidder -> "max|arm_seq"
ARGV[1]  lease in ms
ARGV[2]  rebuild token (unique per attempt)
ARGV[3]  number of state field/value items that follow
ARGV[4 .. 3+n]      state hash field/value pairs (everything except status)
ARGV[4+n ..]        proxies hash field/value pairs (may be empty)

Returns 'EXISTS', 'OUTBOX_PENDING' or 'INITIALISED'.
]]

local state, outbox, proxies = KEYS[1], KEYS[2], KEYS[3]
local lease_ms, token, n_state = tonumber(ARGV[1]), ARGV[2], tonumber(ARGV[3])

local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)

if redis.call('EXISTS', state) == 1 then
  local status = redis.call('HGET', state, 'status')
  local deadline = tonumber(redis.call('HGET', state, 'rebuild_deadline_ms') or '0')
  if status ~= 'rebuilding' or now < deadline then
    return 'EXISTS'
  end
  -- a rebuild whose lease expired: take it over
  redis.call('DEL', state, proxies)
end

if redis.call('XLEN', outbox) > 0 then
  return 'OUTBOX_PENDING'
end

redis.call('HSET', state, unpack(ARGV, 4, 3 + n_state))
redis.call('HSET', state,
  'status', 'rebuilding',
  'rebuild_token', token,
  'rebuild_deadline_ms', string.format('%.0f', now + lease_ms))
redis.call('DEL', proxies)
if #ARGV > 3 + n_state then
  redis.call('HSET', proxies, unpack(ARGV, 4 + n_state))
end
return 'INITIALISED'
