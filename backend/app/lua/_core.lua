--[[
Arbitration core shared by place_bid.lua and set_proxy.lua (included at load time by
app/lua/__init__.py; the including script defines these locals first):

  state     auction:{<id>}:state     live state hash
  outbox    auction:{<id>}:bids      outbox stream
  proxies   auction:{<id>}:proxies   hash bidder -> "max|arm_seq"
  auction_id

Provides int(), reject(), load_state(), min_next(), accept() and resolve(). All amounts are
integer paise held in Lua doubles (< 2^53, validated by the caller) and written back with %.0f.
]]

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

-- live state, filled by load_state() and kept current by accept()
local now, status, start_time, end_time, highest, highest_bidder, seq
-- addressing data for outbid notices (internal; stripped by the service)
local previous_leader
local exhausted = {}
local start_price, min_increment, extension_count, threshold, extension, self_outbid

-- returns false when the state is missing or a rebuild is still unverified
local function load_state()
  local f = redis.call('HMGET', state,
    'status', 'start_time_ms', 'end_time_ms', 'highest_amount', 'highest_bidder', 'sequence',
    'start_price', 'min_increment', 'extension_count', 'threshold_ms', 'extension_ms', 'self_outbid')
  status = f[1]
  if not status or status == 'rebuilding' then
    return false
  end
  -- authoritative clock (DC-3); the caller never supplies a time
  local t = redis.call('TIME')
  now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
  start_time = tonumber(f[2])
  end_time = tonumber(f[3])
  highest = tonumber(f[4])
  highest_bidder = f[5]
  previous_leader = highest_bidder
  seq = tonumber(f[6])
  start_price = tonumber(f[7])
  min_increment = tonumber(f[8])
  extension_count = tonumber(f[9])
  threshold = tonumber(f[10])
  extension = tonumber(f[11])
  self_outbid = f[12] == '1'
  return true
end

-- smallest amount a manual bid or a new maximum must reach (FR-37, FR-46)
local function min_next()
  if seq > 0 then
    return highest + min_increment
  end
  return start_price
end

-- Accept one bid: new highest, next sequence, soft close, outbox entry (FR-39, FR-50).
-- Automatic bids come through here too, so they get the same sequence numbering,
-- soft-close evaluation and durability as manual bids.
local function accept(bid_bidder, bid_amount, bid_type)
  seq = seq + 1
  highest = bid_amount
  highest_bidder = bid_bidder
  local extended = false
  -- soft close (FR-52 to FR-54): against the then-current end time, capped at 10
  if end_time - now <= threshold and extension_count < 10 then
    end_time = end_time + extension
    extension_count = extension_count + 1
    extended = true
  end
  redis.call('HSET', state,
    'highest_amount', int(bid_amount),
    'highest_bidder', bid_bidder,
    'sequence', int(seq),
    'end_time_ms', int(end_time),
    'extension_count', int(extension_count))

  local entry = {
    'kind', 'bid',
    'auction_id', auction_id,
    'bidder_id', bid_bidder,
    'amount', int(bid_amount),
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
  return {
    bidder_id = bid_bidder,
    amount = int(bid_amount),
    sequence_no = int(seq),
    bid_type = bid_type,
    extended = extended,
    end_time_ms = int(end_time),
    extension_count = int(extension_count),
    -- the arbitration time (DC-3), the same value the ledger stores as created_at
    accepted_at_ms = int(now),
  }
end

-- active maxima sorted by max desc, then arm_seq asc (earlier commitment first, FR-49)
local function active_proxies()
  local raw = redis.call('HGETALL', proxies)
  local list = {}
  for i = 1, #raw, 2 do
    local sep = string.find(raw[i + 1], '|', 1, true)
    list[#list + 1] = {
      owner = raw[i],
      max = tonumber(string.sub(raw[i + 1], 1, sep - 1)),
      arm = tonumber(string.sub(raw[i + 1], sep + 1)),
    }
  end
  table.sort(list, function(a, b)
    if a.max ~= b.max then
      return a.max > b.max
    end
    return a.arm < b.arm
  end)
  return list
end

-- A maximum that no longer exceeds the highest bid held by someone else can never bid
-- again: deactivate it (UC-02 5a, FR-51). The leader's own maximum stays armed.
local function deactivate_exhausted(list)
  for _, p in ipairs(list) do
    if p.owner ~= highest_bidder and p.max <= highest then
      redis.call('HDEL', proxies, p.owner)
      exhausted[#exhausted + 1] = p.owner
      redis.call('XADD', outbox, '*',
        'kind', 'proxy_deactivated',
        'auction_id', auction_id,
        'bidder_id', p.owner,
        'arm_seq', int(p.arm),
        'deactivated_at_ms', int(now))
    end
  end
end

-- Proxy resolution after any price-raising event (FR-47 to FR-49), in closed form:
-- at most two automatic bids per trigger, never a per-increment loop.
--   top     highest maximum (ties: earlier arm_seq)
--   second  runner-up maximum, or nil
--   target  where the top maximum ends up: one increment above the competing level
--           (the runner-up maximum or the current highest bid, whichever is higher),
--           capped at its own maximum (FR-48, FR-49)
-- The runner-up's bid at its own maximum is recorded first when it is a valid bid below
-- the target; with equal maxima it is not, and the price settles at the common value.
-- who to tell they were outbid; never sent to a client as-is (FR-51)
local function internal()
  local r = {}
  if previous_leader and previous_leader ~= '' then
    r.previous_leader = previous_leader
  end
  if #exhausted > 0 then
    r.exhausted = exhausted
  end
  if next(r) then
    return r
  end
  return nil
end

local function resolve()
  local autos = {}
  local list = active_proxies()
  local top, second = list[1], list[2]
  if top and (top.owner == highest_bidder or top.max > highest) then
    local h0, l0 = highest, highest_bidder
    local competing = h0
    if second and second.max > competing then
      competing = second.max
    end
    local floor = (competing > 0) and (competing + min_increment) or start_price
    local target = math.min(top.max, floor)
    local challenged = second ~= nil and second.max > h0
    if top.owner ~= l0 or challenged then
      if challenged and second.max < target then
        autos[#autos + 1] = accept(second.owner, second.max, 'automatic')
      end
      autos[#autos + 1] = accept(top.owner, target, 'automatic')
    end
  end
  deactivate_exhausted(list)
  return autos
end
