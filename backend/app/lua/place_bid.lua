#!lua
--[[
Bid arbitration: the single per-auction serialisation point (DC-2, FR-35).
Every bid for an auction is decided here, atomically, against Redis TIME (DC-3), and armed
proxy maxima respond in the same step (FR-47).

KEYS[1]  auction:{<id>}:state                     live state hash
KEYS[2]  auction:{<id>}:bids                      outbox stream
KEYS[3]  auction:{<id>}:req:<bidder>:<request_id> idempotency record
KEYS[4]  auction:{<id>}:proxies                   hash bidder -> "max|arm_seq"
ARGV[1]  auction_id
ARGV[2]  bidder_id
ARGV[3]  amount, integer paise as a decimal string (validated <= 2^53-1 by the caller)
ARGV[4]  request record TTL in ms

Returns a JSON string. Every number in it is a string: cjson keeps only 14 significant digits.
Top-level fields describe the submitter's own bid; automatic_bids (when present) lists the
proxy responses in sequence order. Rejections write nothing.
]]

local state, outbox, req, proxies = KEYS[1], KEYS[2], KEYS[3], KEYS[4]
local auction_id, bidder, amount_str, req_ttl = ARGV[1], ARGV[2], ARGV[3], ARGV[4]
--#include _core

-- a. idempotent retry: same bidder + request_id returns the stored result unchanged
local previous = redis.call('GET', req)
if previous then
  return previous
end

-- b. live state must exist and be verified (a rebuild in progress counts as absent)
-- c. load_state reads the authoritative clock
if not load_state() then
  return reject('NOT_INITIALISED')
end
local amount = tonumber(amount_str)

-- d. auction must be live and not past its end (FR-36)
if status == 'scheduled' or now < start_time then
  return reject('AUCTION_NOT_STARTED')
end
if status ~= 'live' or now >= end_time then
  return reject('AUCTION_CLOSED')
end

-- e. minimum next bid (FR-37); the reason goes to the submitter only (FR-41)
local floor = min_next()
if amount < floor then
  local extra = { min_next = int(floor) }
  if seq > 0 then
    extra.current_highest = int(highest)
  end
  return reject('BID_TOO_LOW', extra)
end

-- f. catch obvious input errors such as an extra digit (FR-37, BR-14)
if amount > 100 * start_price then
  return reject('BID_ABOVE_LIMIT')
end

-- g. self-outbid policy, manual bids only (FR-38)
if self_outbid and bidder == highest_bidder then
  return reject('SELF_OUTBID_NOT_ALLOWED')
end

-- h-j. accept: new highest, next sequence, soft close, outbox entry (FR-39)
local own = accept(bidder, amount, 'manual')

-- armed maxima respond at the same serialisation point (FR-47 to FR-50)
local autos = resolve()

-- k. remember the result for idempotent retries, then acknowledge (FR-40)
local result = {
  decision = 'accepted',
  sequence_no = own.sequence_no,
  amount = own.amount,
  extended = own.extended,
  end_time_ms = own.end_time_ms,
  extension_count = own.extension_count,
}
if #autos > 0 then
  result.automatic_bids = autos
end
local encoded = cjson.encode(result)
redis.call('SET', req, encoded, 'PX', req_ttl)
return encoded
