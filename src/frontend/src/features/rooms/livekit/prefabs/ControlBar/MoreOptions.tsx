import { css } from '@/styled-system/css'
import { ChatToggle } from '../../components/controls/ChatToggle'
import { ParticipantsToggle } from '../../components/controls/ParticipantsToggle'
import { ToolsToggle } from '../../components/controls/ToolsToggle'
import { InfoToggle } from '../../components/controls/InfoToggle'
import { AdminToggle } from '../../components/AdminToggle'

export const MoreOptions = () => (
  <nav
    className={css({
      display: 'flex',
      justifyContent: 'flex-end',
      flex: '1 1 33%',
      alignItems: 'center',
      gap: '0.5rem',
      paddingRight: '0.25rem',
      '@media (max-width: 1099px)': {
        flex: '1 0 100%',
      },
    })}
  >
    <InfoToggle />
    <ParticipantsToggle />
    <ChatToggle />
    <ToolsToggle />
    <AdminToggle />
  </nav>
)
