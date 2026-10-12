#!lua
--[[
Finish a rebuild started by init_state.lua, only if this attempt still owns it.

KEYS[1]  auction:{<id>}:state
KEYS[2]  auction:{<id>}:proxies
ARGV[1]  rebuild token
ARGV[2]  'commit' (go live with ARGV[3] as status) or 'abort' (discard both hashes)
ARGV[3]  real status from Postgres (commit only)

Returns 1 if applied, 0 if the hash is gone or owned by another attempt.
]]

local state, proxies = KEYS[1], KEYS[2]
if redis.call('HGET', state, 'status') ~= 'rebuilding'
  or redis.call('HGET', state, 'rebuild_token') ~= ARGV[1] then
  return 0
end

if ARGV[2] == 'commit' then
  redis.call('HSET', state, 'status', ARGV[3])
  redis.call('HDEL', state, 'rebuild_token', 'rebuild_deadline_ms')
else
  redis.call('DEL', state, proxies)
end
return 1
