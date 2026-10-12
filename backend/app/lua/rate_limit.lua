#!lua
-- fixed-window counter: INCR, and start the expiry on the first hit (NFR-22)
local count = redis.call('INCR', KEYS[1])
if count == 1 then
  redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return count
