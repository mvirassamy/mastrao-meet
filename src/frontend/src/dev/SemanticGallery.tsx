import { useState } from 'react'
import { Button, ToggleButton, Popover } from '@/primitives'
import { Select } from '@/primitives/Select'
import { Checkbox } from '@/primitives/Checkbox'
import { css } from '@/styled-system/css'

const variants = [
  'primary',
  'secondary',
  'outline',
  'ghost',
  'destructive',
] as const

/** Development-only gallery of the actual product primitives. */
export const SemanticGallery = () => {
  const [selected, setSelected] = useState(false)
  return (
    <section className="semantic-gallery">
      <h1>Rôles et états des composants Meet</h1>
      <p>Les commandes ci-dessous sont des démonstrations locales.</p>
      <div className="semantic-gallery-grid">
        {variants.map((variant) => (
          <section key={variant}>
            <h2>{variant}</h2>
            <Button variant={variant}>{variant} · repos</Button>
            <Button variant={variant} isDisabled>
              {variant} · indisponible
            </Button>
            <Button variant={variant} isDisabled loading>
              {variant} · chargement
            </Button>
          </section>
        ))}
      </div>
      <section>
        <h2>Sélection et menus</h2>
        <ToggleButton
          variant="outline"
          isSelected={selected}
          onChange={setSelected}
        >
          {selected ? 'Sous-titres activés' : 'Sous-titres désactivés'}
        </ToggleButton>
        <Popover variant="dark">
          <Button variant="outline">Ouvrir le menu flottant</Button>
          <p>Surface popover, texte popover-foreground.</p>
        </Popover>
        <Select
          aria-label="Appareil de test"
          label="Appareil"
          variant="dark"
          defaultSelectedKey="micro"
          items={[
            { value: 'micro', label: 'Microphone de démonstration' },
            { value: 'casque', label: 'Casque de démonstration' },
          ]}
        />
        <Checkbox>Préférence de démonstration</Checkbox>
      </section>
      <section
        className={css({
          backgroundColor: 'media-surface',
          color: 'media-foreground',
          padding: '1',
        })}
      >
        <h2>Surface média et superposition</h2>
        <div
          className={css({
            backgroundColor: 'media-overlay',
            color: 'media-overlay-foreground',
            padding: '1',
          })}
        >
          Camille · microphone coupé
          <Button
            variant="whiteCircle"
            aria-label="Microphone de démonstration coupé"
          >
            M
          </Button>
        </div>
      </section>
      <section>
        <h2>Statuts</h2>
        <p
          className={css({ backgroundColor: 'info', color: 'info-foreground' })}
        >
          Information
        </p>
        <p
          className={css({
            backgroundColor: 'warning',
            color: 'warning-foreground',
          })}
        >
          Permission nécessaire
        </p>
        <p
          className={css({
            backgroundColor: 'success',
            color: 'success-foreground',
          })}
        >
          Opération réussie
        </p>
        <p
          className={css({
            backgroundColor: 'recording',
            color: 'recording-foreground',
          })}
        >
          Enregistrement · état simulé
        </p>
      </section>
    </section>
  )
}
