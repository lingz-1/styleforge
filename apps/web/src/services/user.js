// 记住当前用户（localStorage 持久化，刷新/重开保持登录状态）
const KEY = 'sf_user_id'

export const getUserId = () => localStorage.getItem(KEY) || 'demo-user'

export const setUserId = (id) => {
  const value = (id || '').trim()
  if (!value) return
  localStorage.setItem(KEY, value)
}
