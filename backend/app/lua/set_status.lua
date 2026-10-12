#!lua
--[[
Atomic status transition of an auction's live state (D9, FR-16, FR-58).

Runs at the same serialisation point as bid arbitration, so no bid can be accepted between the
check and the write. The scheduler uses it for live -> closing: with ARGV[3] = '1' the
transition happens only once Redis TIME (the clock arbitration uses, DC-3) has reached
end_time_ms, so an auction a late bid has just extended stays live.

KEYS[1]  auction:{<id>}:state
ARGV[1]  expected current status
ARGV[2]  new status
ARGV[3]  '1' to require that the end time has passed, '0' otherwise

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
if ARGV[3] == '1' then
  local t = redis.call('TIME')
  local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
  if now < tonumber(redis.call('HGET', state, 'end_time_ms')) then
    return 'NOT_ENDED'
  end
end
redis.call('HSET', state, 'status', ARGV[2])
return 'OK'
