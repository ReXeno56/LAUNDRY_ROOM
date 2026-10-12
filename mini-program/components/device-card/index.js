Component({
  properties: {
    theme: { type: String, value: 'light' }, device: Object, expanded: Boolean, minutes: Number, reminderTime: String, busy: Boolean,
  },
  methods: {
    toggle() { if (!this.data.busy) this.triggerEvent('toggle', this.data.device) },
    changeMinutes(e) { this.triggerEvent('minuteschange', { value: e.detail.value }) },
    confirm() { if (!this.data.busy) this.triggerEvent('confirm', this.data.device) },
  },
})
