#!lua
--[[
Claim the WebSocket slot of a session (CI-3, D3).

One socket per session: every claim takes a new generation from a global counter, so the newest
socket of a session always holds the highest generation and replaces older ones on every
instance. A user may hold at most ARGV[4] sessions at once; reconnecting a session the user
already holds never counts against that cap.

KEYS[1]  ws:user:<user_id>:sessions   sorted set: session id -> expiry (ms)
KEYS[2]  ws:sid:<sid>                 generation of the session's current socket
KEYS[3]  ws:conn_seq                  global generation counter
ARGV[1]  session id
ARGV[2]  now (ms)
ARGV[3]  ttl (ms), refreshed by the presence loop
ARGV[4]  maximum sessions per user

Returns the new generation, or 0 when the user already holds the maximum number of other
sessions.
]]

local sessions, owner, seq = KEYS[1], KEYS[2], KEYS[3]
local sid, now, ttl, cap = ARGV[1], tonumber(ARGV[2]), tonumber(ARGV[3]), tonumber(ARGV[4])

redis.call('ZREMRANGEBYSCORE', sessions, '-inf', now)
if not redis.call('ZSCORE', sessions, sid) and redis.call('ZCARD', sessions) >= cap then
  return 0
end
local gen = redis.call('INCR', seq)
redis.call('ZADD', sessions, now + ttl, sid)
redis.call('PEXPIRE', sessions, ttl)
redis.call('SET', owner, gen, 'PX', ttl)
return gen
