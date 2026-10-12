#!lua
--[[
Set or raise a proxy maximum and resolve it at the bid serialisation point (FR-45 to FR-49).

KEYS[1]  auction:{<id>}:state
KEYS[2]  auction:{<id>}:bids                       outbox stream
KEYS[3]  auction:{<id>}:preq:<bidder>:<request_id> idempotency record
KEYS[4]  auction:{<id>}:proxies                    hash bidder -> "max|arm_seq"
ARGV[1]  auction_id
ARGV[2]  bidder_id
ARGV[3]  maximum, integer paise as a decimal string (validated <= 2^53-1 by the caller)
ARGV[4]  request record TTL in ms

Returns a JSON string; numbers are strings. The maximum is echoed only to its owner (FR-51).
Rejections write nothing.
]]

local state, outbox, req, proxies = KEYS[1], KEYS[2], KEYS[3], KEYS[4]
local auction_id, bidder, max_str, req_ttl = ARGV[1], ARGV[2], ARGV[3], ARGV[4]
--#include _core

-- idempotent retry: same bidder + request_id returns the stored result unchanged
local previous = redis.call('GET', req)
if previous then
  return previous
end
if not load_state() then
  return reject('NOT_INITIALISED')
end
local max = tonumber(max_str)

-- FR-46, UC-02 2b: a maximum for any auction that is not live is AUCTION_CLOSED
if status ~= 'live' or now < start_time or now >= end_time then
  return reject('AUCTION_CLOSED')
end
-- FR-46: must exceed the current highest bid by at least one increment
local floor = min_next()
if max < floor then
  return reject('BELOW_CURRENT_PRICE', { min_next = int(floor) })
end
-- FR-37, BR-14: the 100x sanity limit applies to maxima too
if max > 100 * start_price then
  return reject('BID_ABOVE_LIMIT')
end
-- FR-46: an existing maximum may only be raised
local existing = redis.call('HGET', proxies, bidder)
if existing then
  local sep = string.find(existing, '|', 1, true)
  if max <= tonumber(string.sub(existing, 1, sep - 1)) then
    return reject('PROXY_CANNOT_DECREASE')
  end
end

-- arm: a raise takes a new arm_seq, so an equal-maxima tie goes to whoever committed
-- to that amount first (FR-49)
local arm = redis.call('HINCRBY', state, 'arm_seq', 1)
redis.call('HSET', proxies, bidder, max_str .. '|' .. int(arm))
redis.call('XADD', outbox, '*',
  'kind', 'proxy_set',
  'auction_id', auction_id,
  'bidder_id', bidder,
  'max_amount', max_str,
  'arm_seq', int(arm),
  'armed_at_ms', int(now))

-- FR-47: resolve immediately, atomically, here
local autos = resolve()

local result = {
  decision = 'armed',
  max_amount = max_str,
  arm_seq = int(arm),
  -- false when this maximum was exhausted by the same resolution (outbid on arming)
  active = redis.call('HEXISTS', proxies, bidder) == 1,
}
if #autos > 0 then
  result.automatic_bids = autos
end
result._internal = internal()
local encoded = cjson.encode(result)
redis.call('SET', req, encoded, 'PX', req_ttl)
return encoded
