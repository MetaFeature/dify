export const messages = Object.freeze({
  booking: {
    cancelled: '预约已取消。',
    confirmed: '预约成功。',
    supplemented: '当前时段补约成功，可立即进入平台。',
    waitlisted: '时段已满，已加入候补队列。',
  },
  errors: {
    'bad-request': '请求内容有误，请检查后重试。',
    busy: '服务器当前负载较高，暂时无法补约本时段，请稍后重试。',
    conflict: '当前操作与已有预约冲突，请刷新后重试。',
    forbidden: '当前预约时段尚未生效。',
    generic: '操作失败，请稍后重试。',
    network: '无法连接校园平台，请检查网络后重试。',
    'not-found': '请求的内容不存在。',
    unauthorized: '登录已失效，请重新登录。',
    unavailable: '服务暂时不可用，请稍后重试。',
  },
  loading: {
    slots: '正在读取开放时段…',
  },
  reservations: {
    cancel: '取消预约',
    empty: '暂无预约记录',
    noneDetail: '可从左侧选择一个开放时段',
    statuses: {
      cancelled: '已取消',
      completed: '已完成',
      confirmed: '已确认',
      expired: '已过期',
      waitlisted: '候补中',
    },
  },
  slots: {
    reserve: '预约',
    supplement: '补约当前时段',
    waitlist: '加入候补',
    unavailable: '不可预约',
  },
})
