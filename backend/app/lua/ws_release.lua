#!lua
--[[
Release a session's slot when its socket closes, but only if that socket still owns it: a
replaced socket closing late must not free the slot of its successor (D3).

KEYS[1]  ws:user:<user_id>:sessions
KEYS[2]  ws:sid:<sid>
ARGV[1]  session id
ARGV[2]  generation of the closing socket

Returns 1 if released, 0 if another socket owns the session now.
]]

if redis.call('GET', KEYS[2]) ~= ARGV[2] then
  return 0
end
redis.call('DEL', KEYS[2])
redis.call('ZREM', KEYS[1], ARGV[1])
return 1
