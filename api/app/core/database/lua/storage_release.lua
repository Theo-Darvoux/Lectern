local expiries = KEYS[1]
local sizes = KEYS[2]
local total_key = KEYS[3]
local reservation_id = ARGV[1]

local size = tonumber(redis.call('HGET', sizes, reservation_id)) or 0
local total = tonumber(redis.call('GET', total_key)) or 0
redis.call('HDEL', sizes, reservation_id)
redis.call('ZREM', expiries, reservation_id)
redis.call('SET', total_key, math.max(0, total - size))
return size
