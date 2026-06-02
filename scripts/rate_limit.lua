-- Redis Lua 滑动窗口限流脚本
-- KEYS[1]: 限流 key
-- ARGV[1]: window_seconds
-- ARGV[2]: limit
-- ARGV[3]: now_seconds，传 0 时使用 Redis TIME

local key = KEYS[1]
local window_seconds = tonumber(ARGV[1])
local limit = tonumber(ARGV[2])
local input_now_seconds = tonumber(ARGV[3])

local now_seconds = input_now_seconds
if now_seconds == nil or now_seconds <= 0 then
  local now_reply = redis.call("TIME")
  now_seconds = tonumber(now_reply[1])
end

local window_start = now_seconds - window_seconds
redis.call("ZREMRANGEBYSCORE", key, 0, window_start)

local current_count = tonumber(redis.call("ZCARD", key))
if current_count >= limit then
  local oldest_entries = redis.call("ZRANGE", key, 0, 0, "WITHSCORES")
  local retry_after = window_seconds
  if oldest_entries ~= nil and #oldest_entries >= 2 then
    local oldest_score = tonumber(oldest_entries[2])
    retry_after = math.max(1, window_seconds - (now_seconds - oldest_score))
  end
  return {0, retry_after}
end

local member = tostring(now_seconds) .. "-" .. redis.sha1hex(tostring(now_seconds) .. "-" .. tostring(math.random()))
redis.call("ZADD", key, now_seconds, member)
redis.call("EXPIRE", key, window_seconds + 1)
return {1, 0}
