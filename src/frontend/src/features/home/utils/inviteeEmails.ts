// Match the canonical Core z.email() contract.
const EMAIL =
  /^(?!\.)(?!.*\.\.)([A-Za-z0-9_'+\-.]*)[A-Za-z0-9_+-]@([A-Za-z0-9][A-Za-z0-9-]*\.)+[A-Za-z]{2,}$/u
export const MAX_INVITEE_EMAILS = 50

export const inviteeEmailParts = (value: string) =>
  value.split(/[\s,;]+/u).filter(Boolean)

/** Keep the entire draft editable; creation rejects any invalid address. */
export const readInviteeEmails = (value: string) => {
  const emails = new Set<string>()
  const invalidEmails: string[] = []
  for (const part of inviteeEmailParts(value)) {
    const email = part.toLowerCase()
    if (email.length > 254 || !EMAIL.test(email)) invalidEmails.push(part)
    else emails.add(email)
  }
  return { emails: [...emails], invalidEmails }
}
