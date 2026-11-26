if (!window.messages_csrf_retry_patched) {
  window.messages_csrf_retry_patched = true
  var csrfPatchTries = 0
  var csrfPatchTimer = setInterval(function() {
    csrfPatchTries = csrfPatchTries + 1
    if (frappe && frappe.call && frappe.request && frappe.request.error_handlers && !frappe.messages_csrf_retry_patched) {
      clearInterval(csrfPatchTimer)
      frappe.messages_csrf_retry_patched = true
      var originalCall = frappe.call
      frappe.call = function(opts) {
        var callOpts = opts
        if (typeof opts === 'string') {
          callOpts = {
            method: arguments[0],
            args: arguments[1],
            callback: arguments[2],
            headers: arguments[3]
          }
        }
        if (!callOpts || typeof callOpts !== 'object') {
          return originalCall.apply(frappe, arguments)
        }
        var handlerMap = callOpts.error_handlers || {}
        var previousHandler = handlerMap.CSRFTokenError
        var retryHandler = function(response) {
          if (previousHandler) {
            previousHandler(response)
          }
          if (callOpts.messages_csrf_retried) {
            return
          }
          callOpts.messages_csrf_retried = true
          fetch('/api/method/backend_custom_app.api.auth.get_csrf_token', {
            credentials: 'include',
            headers: { Accept: 'application/json' }
          })
            .then(function(res) {
              return res.json()
            })
            .then(function(body) {
              var token = body && (body.message || body.csrf_token)
              if (!token) {
                frappe.msgprint('Invalid Request')
                return
              }
              frappe.csrf_token = token
              if (window.csrf_token) {
                window.csrf_token = token
              }
              originalCall(callOpts)
            })
            .catch(function() {
              frappe.msgprint('Invalid Request')
            })
        }
        handlerMap.CSRFTokenError = retryHandler
        callOpts.error_handlers = handlerMap
        return originalCall(callOpts)
      }
    } else if (csrfPatchTries > 200) {
      clearInterval(csrfPatchTimer)
    }
  }, 50)
}

if (!window.messages_toast_listener_bound) {
  window.messages_toast_listener_bound = true
  var tries = 0
  var timer = setInterval(function() {
    tries = tries + 1
    if (frappe && frappe.realtime && frappe.realtime.socket) {
      clearInterval(timer)
      var lastSoundAt = 0
      var escapeMessageHtml = function(value) {
        var text = String(value == null ? '' : value)
        var out = ''
        var i = 0
        while (i < text.length) {
          var ch = text.charAt(i)
          if (ch === '<') { out = out + '&lt;' }
          else if (ch === '>') { out = out + '&gt;' }
          else if (ch === '&') { out = out + '&amp;' }
          else if (ch === '"') { out = out + '&quot;' }
          else if (ch === "'") { out = out + '&#39;' }
          else { out = out + ch }
          i = i + 1
        }
        return out
      }
      var unlockDeskSound = function() {
        var audio = document.querySelector('#sound-alert')
        if (!audio || !audio.play) {
          return
        }
        var previousVolume = audio.volume
        audio.volume = 0
        var played = audio.play()
        var finish = function() {
          audio.pause()
          audio.currentTime = 0
          audio.volume = previousVolume
        }
        if (played && played.then) {
          played.then(finish, finish)
        } else {
          finish()
        }
      }
      window.addEventListener('click', unlockDeskSound, true)
      window.addEventListener('keydown', unlockDeskSound, true)
      var playDeskNotificationSound = function() {
        if (window.localStorage.getItem('backend_messages_sound_muted') === '1') {
          return
        }
        if (frappe.boot && frappe.boot.user && frappe.boot.user.mute_sounds) {
          return
        }
        var now = Date.now()
        if (now - lastSoundAt < 2000) {
          return
        }
        lastSoundAt = now
        frappe.utils.play_sound('alert')
      }
      var visibleDeskGroup = function() {
        var path = window.location.pathname || ''
        var route = frappe.get_route ? frappe.get_route() : []
        var inMessages = path.indexOf('/desk/messages') >= 0 || (route.length > 0 && route[0] === 'messages')
        if (!inMessages) {
          return ''
        }
        if (window.location.search) {
          var params = new URLSearchParams(window.location.search)
          return params.get('group') || ''
        }
        if (route.length > 1 && route[1]) {
          return decodeURIComponent(route[1])
        }
        return ''
      }
      frappe.realtime.on('messages_new_message_toast', function(data) {
        if (!data || !data.sender_name) {
          return
        }
        if (data.sender && data.sender === frappe.session.user) {
          return
        }
        var openGroup = visibleDeskGroup()
        if (!document.hidden && openGroup && data.group === openGroup) {
          return
        }
        playDeskNotificationSound()
        var inMessages = false
        if (window.location.pathname.indexOf('/desk/messages') >= 0) {
          inMessages = true
        }
        if (!inMessages && frappe.get_route) {
          var route = frappe.get_route()
          if (route.length > 0 && route[0] === 'messages') {
            inMessages = true
          }
        }
        if (inMessages || !frappe.show_alert) {
          return
        }
        var message = escapeMessageHtml(data.sender_name) + ': ' + escapeMessageHtml(data.preview || '')
        frappe.show_alert({
          message: message,
          subtitle: escapeMessageHtml(data.group_name || 'New message'),
          body: '<button class="btn btn-xs btn-default" data-action="open-messages">Open Messages</button>',
          indicator: 'blue'
        }, 8, {
          'open-messages': function() {
            if (data.group) {
              window.location.href = '/desk/messages?group=' + encodeURIComponent(data.group)
            } else {
              window.location.href = '/desk/messages'
            }
          }
        })
      })
    } else if (tries > 100) {
      clearInterval(timer)
    }
  }, 100)
}
