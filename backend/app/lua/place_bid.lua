#!lua
--[[
Bid arbitration: the single per-auction serialisation point (DC-2, FR-35).
Every bid for an auction is decided here, atomically, against Redis TIME (DC-3).

KEYS[1]  auction:{<id>}:state                     live state hash
KEYS[2]  auction:{<id>}:bids                      outbox stream
KEYS[3]  auction:{<id>}:req:<bidder>:<request_id> idempotency record
ARGV[1]  auction_id
ARGV[2]  bidder_id
ARGV[3]  amount, integer paise as a decimal string (validated <= 2^53-1 by the caller)
ARGV[4]  request record TTL in ms

Returns a JSON string. Every number in it is a string: cjson keeps only 14 significant digits.
Rejections write nothing.
]]

local state, outbox, req = KEYS[1], KEYS[2], KEYS[3]
local auction_id, bidder, amount_str, req_ttl = ARGV[1], ARGV[2], ARGV[3], ARGV[4]

-- integer-valued double -> decimal string, never exponent notation
local function int(n)
  return string.format('%.0f', n)
end

local function reject(code, extra)
  local r = extra or {}
  r.decision = 'rejected'
  r.code = code
  return cjson.encode(r)
end

-- a. idempotent retry: same bidder + request_id returns the stored result unchanged
local previous = redis.call('GET', req)
if previous then
  return previous
end

-- b. live state must exist and be verified (a rebuild in progress counts as absent)
local f = redis.call('HMGET', state,
  'status', 'start_time_ms', 'end_time_ms', 'highest_amount', 'highest_bidder', 'sequence',
  'start_price', 'min_increment', 'extension_count', 'threshold_ms', 'extension_ms', 'self_outbid')
local status = f[1]
if not status or status == 'rebuilding' then
  return reject('NOT_INITIALISED')
end

-- c. authoritative clock (DC-3); the caller never supplies a time
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)

local start_time = tonumber(f[2])
local end_time = tonumber(f[3])
local highest = tonumber(f[4])
local highest_bidder = f[5]
local seq = tonumber(f[6])
local start_price = tonumber(f[7])
local min_increment = tonumber(f[8])
local extension_count = tonumber(f[9])
local threshold = tonumber(f[10])
local extension = tonumber(f[11])
local self_outbid = f[12] == '1'
local amount = tonumber(amount_str)

-- d. auction must be live and not past its end (FR-36)
if status == 'scheduled' or now < start_time then
  return reject('AUCTION_NOT_STARTED')
end
if status ~= 'live' or now >= end_time then
  return reject('AUCTION_CLOSED')
end

-- e. minimum next bid (FR-37); the reason goes to the submitter only (FR-41)
local min_next = start_price
if seq > 0 then
  min_next = highest + min_increment
end
if amount < min_next then
  local extra = { min_next = int(min_next) }
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

-- h-j. accept one bid: new highest, next sequence, soft close, outbox entry (FR-39).
-- Phase 4 calls this again for each automatic proxy bid, with bid_type 'automatic'.
local function accept(bid_bidder, bid_amount_str, bid_type)
  seq = seq + 1
  local extended = false
  -- soft close (FR-52 to FR-54): evaluated against the then-current end time, capped at 10
  if end_time - now <= threshold and extension_count < 10 then
    end_time = end_time + extension
    extension_count = extension_count + 1
    extended = true
  end
  redis.call('HSET', state,
    'highest_amount', bid_amount_str,
    'highest_bidder', bid_bidder,
    'sequence', int(seq),
    'end_time_ms', int(end_time),
    'extension_count', int(extension_count))

  local entry = {
    'auction_id', auction_id,
    'bidder_id', bid_bidder,
    'amount', bid_amount_str,
    'sequence_no', int(seq),
    'bid_type', bid_type,
    'accepted_at_ms', int(now),
  }
  if extended then
    -- FR-55: the new end time travels with the bid that caused it
    table.insert(entry, 'end_time_after')
    table.insert(entry, int(end_time))
    table.insert(entry, 'extension_count')
    table.insert(entry, int(extension_count))
  end
  redis.call('XADD', outbox, '*', unpack(entry))
  return { sequence_no = int(seq), amount = bid_amount_str, extended = extended }
end

local accepted = accept(bidder, amount_str, 'manual')

-- Phase 4: proxy resolution goes here, calling accept(proxy_bidder, amount, 'automatic').

-- k. remember the result for idempotent retries, then acknowledge (FR-40)
local result = cjson.encode({
  decision = 'accepted',
  sequence_no = accepted.sequence_no,
  amount = accepted.amount,
  extended = accepted.extended,
  end_time_ms = int(end_time),
  extension_count = int(extension_count),
})
redis.call('SET', req, result, 'PX', req_ttl)
return result
