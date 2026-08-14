export const roomIdPattern = '[a-z]{3}-[a-z]{4}-[a-z]{3}'
export const mastraoRoomIdPattern = 'room_[a-f0-9]{32}'

// Case-insensitive and with optional hyphens
export const flexibleRoomIdPattern = `(?:[a-zA-Z0-9]{3}-?[a-zA-Z0-9]{4}-?[a-zA-Z0-9]{3}|${mastraoRoomIdPattern})`

const roomRegex = new RegExp(`^(?:${roomIdPattern}|${mastraoRoomIdPattern})$`)
const mastraoRoomRegex = new RegExp(`^${mastraoRoomIdPattern}$`)

export const isMastraoRoomId = (roomId: string) => mastraoRoomRegex.test(roomId)

export const isRoomValid = (roomIdOrUrl: string) =>
  roomRegex.test(roomIdOrUrl) ||
  new RegExp(
    `^${window.location.origin}/(?:${roomIdPattern}|${mastraoRoomIdPattern})$`
  ).test(roomIdOrUrl)

export const normalizeRoomId = (roomId: string) => {
  const cleanId = roomId.toLowerCase().replace(/-/g, '')
  if (cleanId.length === 10) {
    return `${cleanId.slice(0, 3)}-${cleanId.slice(3, 7)}-${cleanId.slice(7, 10)}`
  }
  return roomId
}
