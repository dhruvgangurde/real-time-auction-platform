#!lua
-- NFR-8 counters: add to the bucket of the current second (or minute) by Redis TIME, never an
-- app clock (DC-3). KEYS[1] is the bucket prefix; ARGV[1] the bucket width in seconds, ARGV[2]
-- the TTL (housekeeping), then field, increment pairs.
local now = tonumber(redis.call('TIME')[1])
local width = tonumber(ARGV[1])
local key = KEYS[1] .. ':' .. (now - now % width)
for i = 3, #ARGV, 2 do
  redis.call('HINCRBY', key, ARGV[i], ARGV[i + 1])
end
redis.call('EXPIRE', key, ARGV[2])
return now
