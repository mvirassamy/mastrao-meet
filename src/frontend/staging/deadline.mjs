export async function within(promise, milliseconds) {
  let timer
  try {
    return await Promise.race([
      promise,
      new Promise((_, reject) => {
        timer = setTimeout(
          () => reject(new Error('Browser operation deadline exceeded')),
          milliseconds
        )
      }),
    ])
  } finally {
    clearTimeout(timer)
  }
}
