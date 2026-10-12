#!lua
--[[
Atomic status transition of an auction's live state (D9, FR-16, FR-58, FR-63).

Runs at the same serialisation point as bid arbitration, so no bid can be accepted between the
check and the write.

KEYS[1]  auction:{<id>}:state
ARGV[1]  expected current status
ARGV[2]  new status
ARGV[3]  '1'       transition only once Redis TIME (DC-3) has reached end_time_ms (scheduler)
         '0'       no time condition (closing -> terminal after settlement; admin cancel)
         'end_now' administrator force-close: also end the auction now, moving end_time_ms
                   back to Redis TIME when it is later, so the execution-time re-check (FR-58)
                   holds and the forced end time can be recorded in Postgres

Returns 'OK', 'MISSING' (no live state), 'NOT_ENDED' or 'WRONG_STATUS:<status>' (including
'rebuilding'). Only 'OK' writes.
]]

local state = KEYS[1]
local status = redis.call('HGET', state, 'status')
if not status then
  return 'MISSING'
end
if status ~= ARGV[1] then
  return 'WRONG_STATUS:' .. status
end
if ARGV[3] == '1' or ARGV[3] == 'end_now' then
  local t = redis.call('TIME')
  local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
  local end_time = tonumber(redis.call('HGET', state, 'end_time_ms'))
  if ARGV[3] == '1' and now < end_time then
    return 'NOT_ENDED'
  end
  if ARGV[3] == 'end_now' and now < end_time then
    redis.call('HSET', state, 'end_time_ms', string.format('%.0f', now))
  end
end
redis.call('HSET', state, 'status', ARGV[2])
return 'OK'
